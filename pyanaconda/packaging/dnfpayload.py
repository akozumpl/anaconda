# dnfpayload.py
# DNF/rpm software payload management.
#
# Copyright (C) 2013  Red Hat, Inc.
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# the GNU General Public License v.2, or (at your option) any later version.
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY expressed or implied, including the implied warranties of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.  You should have received a copy of the
# GNU General Public License along with this program; if not, write to the
# Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301, USA.  Any Red Hat trademarks that are incorporated in the
# source code or documentation are not subject to the GNU General Public
# License and may only be used or replicated with the express permission of
# Red Hat, Inc.
#
# Red Hat Author(s): Ales Kozumplik <akozumpl@redhat.com>
#

from pyanaconda.flags import flags
from pyanaconda.progress import progressQ

import logging
import multiprocessing
import pyanaconda.constants as constants
import pyanaconda.errors as errors
import pyanaconda.packaging as packaging
import sys

log = logging.getLogger("packaging")

try:
    import rpm
    import dnf
except ImportError as e:
    log.error("dnfpayload: component import failed: %s" % e)
    rpm = dnf=  None

DEFAULT_REPOS = [constants.productName.lower(), "rawhide"]
DNF_CACHE_DIR = '/tmp/dnf.cache'

def do_transaction(base):
    base.do_transaction()

class DNFPayload(packaging.PackagePayload):
    def __init__(self, data):
        packaging.PackagePayload.__init__(self, data)
        if rpm is None or dnf is None:
            raise packaging.PayloadError("unsupported payload type")

        self._base = None
        self._required_groups = []
        self._required_pkgs = []
        self._configure()

    def _add_repo(self, repo):
        dnf_repo = self._base.repos.add_repo(repo.name, repo.baseurl)
        dnf_repo.enable()
        try:
            dnf_repo.get_primary_xml()
        except dnf.RepoError as e:
            raise MetadataError(e.value)

    def _apply_selections(self):
        self._select_group('core')
        for pkg_name in self.data.packages.packageList:
            try:
                self._install_package(pkg_name)
            except packaging.NoSuchPackage as e:
                self._miss(e)

        for group in self.data.packages.groupList:
            try:
                default = group.include in (constants.GROUP_ALL,
                                            constants.GROUP_DEFAULT)
                optional = group.include == constants.GROUP_ALL
                self._select_group(group.name, default=default, optional=optional)
            except packaging.NoSuchGroup as e:
                self._miss(e)

        map(self._install_package, self._required_pkgs)
        map(self._select_group, self._required_groups)

    def _configure(self):
        self._base = dnf.Base()
        conf = self._base.conf
        conf.persistdir = DNF_CACHE_DIR
        self._base.cache_c.prefix = DNF_CACHE_DIR
        self._base.cache_c.suffix = 'default'
        conf.logdir = '/tmp/payload-logs'
        self._base.logging.setup_from_dnf_conf(conf)

        conf.installroot = constants.ROOT_PATH
        conf.releasever = self._getReleaseVersion(None)

    def _install_package(self, pkg_name):
        cnt = self._base.install(pkg_name)
        if not cnt:
            raise packaging.NoSuchPackage(pkg_name)

    def _miss(self, exn):
        if self.data.packages.handleMissing == constants.KS_MISSING_IGNORE:
            return

        if errors.errorHandler.cb(exn, str(exn)) == constants.ERROR_RAISE:
            # The progress bar polls kind of slowly, thus installation could
            # still continue for a bit before the quit message is processed.
            # Doing a sys.exit also ensures the running thread quits before
            # it can do anything else.
            progressQ.send_quit(1)
            sys.exit(1)

    def _select_group(self, group_id, default=True, optional=False):
        grp = self._base.comps.group_by_pattern(group_id)
        if grp is None:
            raise packaging.NoSuchGroup(group_id)
        types = {'mandatory'}
        if default:
            types.add('default')
        if optional:
            types.add('optional')
        self._base.select_group(grp, types)

    def _sync_metadata(self, dnf_repo):
        try:
            dnf_repo.load()
        except dnf.exceptions.RepoError as e:
            raise MetadataError(str(e))

    @property
    def addOns(self):
        # addon repos via kickstart
        return [r.name for r in self.data.repo.dataList()]

    @property
    def baseRepo(self):
        repo_names = [constants.BASE_REPO_NAME] + DEFAULT_REPOS
        for repo in self._base.repos.iter_enabled():
            if repo.id in repo_names:
                return repo.id
        return None

    @property
    def environments(self):
        environments = self._base.comps.environments_iter
        return [e.id for e in environments]

    @property
    def repos(self):
        # known repo ids
        return [r.id for r in self._base.repos.values()]

    def checkSoftwareSelection(self):
        log.info("checking software selection")
        self._apply_selections()
        res = self._base.build_transaction()
        assert res == 2
        log.info("%d packages selected totalling %s" %
                 (len(self._base.transaction), 0))

    def disableRepo(self, repo_id):
        self._base.repos[repo_id].disable()

    def enableRepo(self, repo_id):
        self._base.repos[repo_id].enable()

    def environmentDescription(self, environmentid):
        env = self._base.comps.environment_by_pattern(environmentid)
        if env is None:
            print([e.id for e in self._base.comps.environments])
            raise packaging.NoSuchGroup(environmentid)
        return (env.ui_name, env.ui_description)

    def gatherRepoMetadata(self):
        map(self._sync_metadata, self._base.repos.values())
        self._base.activate_sack(load_system_repo=False)
        self._base.read_comps()

    def install(self):
        self.checkSoftwareSelection()

        process = multiprocessing.Process(target=do_transaction,
                                          args=(self._base,))
        process.start()
        # readout the progress here
        process.join()

    def preInstall(self, packages=None, groups=None):
        super(DNFPayload, self).preInstall()
        self._required_pkgs = packages
        self._required_groups = groups

    def release(self):
        pass

    def setup(self, storage):
        # must end up with the base repo (and its metadata) ready
        super(DNFPayload, self).setup(storage)
        self.updateBaseRepo()
        self.gatherRepoMetadata()

    def updateBaseRepo(self, fallback=True, root=None, checkmount=True):
        method = self.data.method
        assert(method.method == "url")

        url = method.url
        self._base.conf.releasever = self._getReleaseVersion(url)
        repo = self._base.build_repo(constants.BASE_REPO_NAME)
        repo.baseurl = [url]
        repo.mirrorlist = method.mirrorlist
        repo.sslverify = not (method.noverifyssl or flags.noverifyssl)

        self._base.repos.add(repo)
