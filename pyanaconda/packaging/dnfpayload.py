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

from blivet.size import Size
from pyanaconda.flags import flags
from pyanaconda.i18n import _
from pyanaconda.progress import progressQ

import logging
import multiprocessing
import pyanaconda.constants as constants
import pyanaconda.errors as errors
import pyanaconda.packaging as packaging
import sys

log = logging.getLogger("packaging")

try:
    import dnf
    import dnf.exceptions
    import dnf.output
    import rpm
except ImportError as e:
    log.error("dnfpayload: component import failed: %s", e)
    dnf = None
    rpm = None

DEFAULT_REPOS = [constants.productName.lower(), "rawhide"]
DNF_CACHE_DIR = '/tmp/dnf.cache'
REPO_DIRS = ['/etc/yum.repos.d',
             '/etc/anaconda.repos.d',
             '/tmp/updates/anaconda.repos.d',
             '/tmp/product/anaconda.repos.d']

class PayloadRPMDisplay(dnf.output.LoggingTransactionDisplay):
    def __init__(self, queue):
        super(PayloadRPMDisplay, self).__init__()
        self._queue = queue
        self._last_ts = None
        self.cnt = 0

    def event(self, package, action, te_current, te_total, ts_current, ts_total):
        if action == self.PKG_INSTALL and te_current == 0:
            # do not report same package twice
            if self._last_ts == ts_current:
                return
            self._last_ts = ts_current

            msg = '%s.%s (%d/%d)' % \
                (package.name, package.arch, ts_current, ts_total)
            self.cnt += 1
            self._queue.put(('install', msg))
        elif action == self.TRANS_POST:
            self._queue.put(('post', None))

def do_transaction(base, queue):
    display = PayloadRPMDisplay(queue)
    base.do_transaction(display=display)

class DNFPayload(packaging.PackagePayload):
    def __init__(self, data):
        packaging.PackagePayload.__init__(self, data)
        if rpm is None or dnf is None:
            raise packaging.PayloadError("unsupported payload type")

        self._base = None
        self._required_groups = []
        self._required_pkgs = []
        self._configure()

    def _add_repo(self, ksrepo):
        repo = self._base.build_repo(ksrepo.name)
        url = ksrepo.baseurl
        if url:
            repo.baseurl = [url]
        repo.mirrorlist = ksrepo.mirrorlist
        repo.sslverify = not (ksrepo.noverifyssl or flags.noverifyssl)
        repo.enable()
        self._base.repos.add(repo)

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

    def _bump_tx_id(self):
        if self.txID is None:
            self.txID = 1
        else:
            self.txID += 1
        return self.txID

    def _configure(self):
        self._base = dnf.Base()
        conf = self._base.conf
        conf.persistdir = DNF_CACHE_DIR
        self._base.cache_c.prefix = DNF_CACHE_DIR
        self._base.cache_c.suffix = 'default'
        conf.logdir = '/tmp/payload-logs'
        # disable console output completely:
        conf.debuglevel = 0
        conf.errorlevel = 0
        self._base.logging.setup_from_dnf_conf(conf)

        conf.installroot = constants.ROOT_PATH
        conf.releasever = self._getReleaseVersion(None)

        conf.reposdir = REPO_DIRS
        log.info('Loading repositories config on the filesystem.')
        self._base.read_all_repos()
        log.info('Done: Loading repositories config on the filesystem.')

    def _install_package(self, pkg_name):
        cnt = self._base.install(pkg_name)
        if not cnt:
            raise packaging.NoSuchPackage(pkg_name)

    def _miss(self, exn):
        if self.data.packages.handleMissing == constants.KS_MISSING_IGNORE:
            return

        if errors.errorHandler.cb(exn, str(exn)) == errors.ERROR_RAISE:
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
            raise packaging.MetadataError(str(e))

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
        return [env.id for env in environments]

    @property
    def groups(self):
        groups = self._base.comps.groups_iter
        return [g.id for g in groups]

    @property
    def mirrorEnabled(self):
        return False

    @property
    def repos(self):
        # known repo ids
        return [r.id for r in self._base.repos.values()]

    @property
    def spaceRequired(self):
        transaction = self._base.transaction
        if transaction is None:
            return Size(spec="3000 MB")

        size = sum(tsi.installed.installsize for tsi in transaction)
        # add 35% to account for the fact that the above method is laughably
        # inaccurate:
        size *= 1.35
        return Size(size)

    def _isGroupVisible(self, grpid):
        grp = self._base.comps.group_by_pattern(grpid)
        if grp is None:
            raise packaging.NoSuchGroup(grpid)
        return grp.visible

    def _groupHasInstallableMembers(self, grpid):
        return True

    def checkSoftwareSelection(self):
        log.info("checking software selection")
        self._bump_tx_id()
        self._apply_selections()

        log.info("checking dependencies")
        try:
            if self._base.build_transaction():
                log.debug("success")
            else:
                log.debug("empty transaction")
        except dnf.exceptions.DepsolveError as e:
            msg = str(e)
            log.warning(msg)
            raise packaging.DependencyError([msg])

        log.info("%d packages selected totalling %s",
                 len(self._base.transaction), self.spaceRequired)

    def disableRepo(self, repo_id):
        log.info("Disabling '%s'", repo_id)
        self._base.repos[repo_id].disable()
        super(DNFPayload, self).disableRepo(repo_id)

    def enableRepo(self, repo_id):
        self._base.repos[repo_id].enable()
        super(DNFPayload, self).enableRepo(repo_id)

    def environmentDescription(self, environmentid):
        env = self._base.comps.environment_by_pattern(environmentid)
        if env is None:
            raise packaging.NoSuchGroup(environmentid)
        return (env.ui_name, env.ui_description)

    def environmentHasOption(self, environmentid, grpid):
        env = self._base.comps.environment_by_pattern(environmentid)
        if env is None:
            raise packaging.NoSuchGroup(environmentid)
        return grpid in env.option_ids

    def environmentOptionIsDefault(self, environmentid, grpid):
        env = self._base.comps.environment_by_pattern(environmentid)
        if env is None:
            raise packaging.NoSuchGroup(environmentid)
        return False

    def groupDescription(self, grpid):
        """ Return name/description tuple for the group specified by id. """
        grp = self._base.comps.group_by_pattern(grpid)
        if grp is None:
            raise packaging.NoSuchGroup(grpid)
        return (grp.ui_name, grp.ui_description)

    def gatherRepoMetadata(self):
        map(self._sync_metadata, self._base.repos.iter_enabled())
        self._base.activate_sack(load_system_repo=False)
        self._base.read_comps()

    def install(self):
        progressQ.send_message(_('Starting package installation process'))
        if self.install_device:
            self._setupMedia(self.install_device)
        try:
            self.checkSoftwareSelection()
        except packaging.DependencyError:
            if errors.errorHandler.cb(e) == errors.ERROR_RAISE:
                progressQ.send_quit(1)
                sys.exit(1)

        pkgs_to_download = self._base.transaction.install_set
        log.info('Downloading pacakges.')
        self._base.download_packages(pkgs_to_download)
        log.info('Downloading packages finished.')

        pre_msg = _("Preparing transaction from installation source")
        progressQ.send_message(pre_msg)

        queue = multiprocessing.Queue()
        process = multiprocessing.Process(target=do_transaction,
                                          args=(self._base,queue))
        process.start()
        (token, msg) = queue.get()
        while token != 'post':
            if token == 'install':
                msg = _("Installing %s") % msg
                progressQ.send_message(msg)
            (token, msg) = queue.get()

        post_msg = _("Performing post-installation setup tasks")
        progressQ.send_message(post_msg)
        process.join()

    def isRepoEnabled(self, repo_id):
        try:
            return self._base.repos[repo_id].enabled
        except (dnf.exceptions.RepoError, KeyError):
            return super(DNFPayload, self).isRepoEnabled(repo_id)

    def preInstall(self, packages=None, groups=None):
        super(DNFPayload, self).preInstall()
        self._required_pkgs = packages
        self._required_groups = groups

    def release(self):
        pass

    def reset(self, root=None):
        super(DNFPayload, self).reset()
        self.txID = None

    def selectEnvironment(self, environmentid):
        env = self._base.comps.environment_by_pattern(environmentid)
        map(self.selectGroup, env.group_ids)

    def setup(self, storage):
        # must end up with the base repo (and its metadata) ready
        super(DNFPayload, self).setup(storage)
        self.updateBaseRepo()
        self.gatherRepoMetadata()

    def updateBaseRepo(self, fallback=True, root=None, checkmount=True):
        log.info('configuring base repo')
        url, mirrorlist, sslverify = self._setupInstallDevice(self.storage,
                                                              checkmount)
        method = self.data.method
        if method.method:
            self._base.conf.releasever = self._getReleaseVersion(url)
            if url or mirrorlist:
                base_ksrepo = self.data.RepoData(
                    name=constants.BASE_REPO_NAME, baseurl=url,
                    mirrorlist=mirrorlist, noverifyssl=not sslverify)
                self._add_repo(base_ksrepo)
            else:
                log.debug("disabling ksdata method, doesn't provide a valid repo")
                method.method = None

        for ksrepo in self.data.repo.dataList():
            self._add_repo(ksrepo)

        ksnames = [r.name for r in self.data.repo.dataList()]
        ksnames.append(constants.BASE_REPO_NAME)
        for repo in self._base.repos.iter_enabled():
            id_ = repo.id
            if 'source' in id_ or 'debuginfo' in id_:
                self.disableRepo(id_)
            elif constants.isFinal and 'rawhide' in id_:
                self.disableRepo(id_)
            elif method.method and id_ not in ksnames:
                self.disableRepo(id_)
