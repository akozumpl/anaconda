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

import logging
import pyanaconda.constants as constants
import pyanaconda.packaging as packaging

log = logging.getLogger("packaging")

try:
    import rpm
    import dnf
except ImportError as e:
    log.error("dnfpayload: component import failed: %s" % e)
    rpm = dnf=  None

DEFAULT_REPOS = [constants.productName.lower(), "rawhide"]
DNF_CACHE_DIR = '/tmp/dnf.cache'

class DNFPayload(packaging.PackagePayload):
    def __init__(self, data):
        packaging.PackagePayload.__init__(self, data)
        if rpm is None or dnf is None:
            raise packaging.PayloadError("unsupported payload type")

        self._base = None
        self._configure()

    def _add_repo(self, repo):
        dnf_repo = self._base.repos.add_repo(repo.name, repo.baseurl)
        dnf_repo.enable()
        try:
            dnf_repo.get_primary_xml()
        except dnf.RepoError as e:
            raise MetadataError(e.value)

    def _configure(self):
        self._base = dnf.Base()
        self._base.conf.persistdir = DNF_CACHE_DIR
        self._base.cache_c.prefix = DNF_CACHE_DIR
        self._base.cache_c.suffix = 'default'

    def _sync_metadata(self, dnf_repo):
        try:
            dnf_repo.load()
        except dnf.exceptions.RepoError as e:
            raise MetadataError(str(e))

    def reset(self, root=None):
        # Called any time to reset the instance.
        self._configure()

    def setup(self, storage):
        # must end up with the base repo (and its metadata) ready
        super(DNFPayload, self).setup(storage)
        self.updateBaseRepo()
        self.gatherRepoMetadata()

    def release(self):
        pass
        # self._base.drop_sack()
        # self._base.drop_repos()

    @property
    def repos(self):
        # known repo ids
        return [r.id for r in self._base.repos.values()]

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

    def gatherRepoMetadata(self):
        map(self._sync_metadata, self._base.repos.values())

    def updateBaseRepo(self, fallback=True, root=None, checkmount=True):
        method = self.data.method
        assert(method.method == "url")

        repo = self._base.build_repo(constants.BASE_REPO_NAME)
        repo.baseurl = [method.url]
        repo.mirrorlist = method.mirrorlist
        repo.sslverify = not (method.noverifyssl or flags.noverifyssl)

        self._base.repos.add(repo)

    def configureAddOnRepo(self, repo):
        # responsible for raising NoNetworkError
        return self._add_repo(repo)

    def addRepo(self, repo):
        super(DNFPayload, self).addRepo(repo)
        return self._add_repo(repo)

    def removeRepo(self, repo_id):
        super(DNFPayload, self).removeRepo(repo_id)
        self._base.repos.remove(repo_id)

    def disableRepo(self, repo_id):
        self._base.repos[repo_id].disable()

    def enableRepo(self, repo_id):
        self._base.repos[repo_id].enable()

    def install(self):
        self._base.build_transaction()
        self._base.do_transaction()
