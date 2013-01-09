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

import pyanaconda.packaging as packaging
import pyanaconda.constants as constants

try:
    import rpm
    import dnf
except ImportError as e:
    log.error("dnfpayload: component import failed: %s" % e)

default_repos = [productName.lower(), "rawhide"]

class DNFPayload(packaging.PackagePayload):
    def _sync_metadata(self, dnf_repo):
        dnf_repo.metadata.get_primary_xml()
        dnf_repo.metadata.get_groups()

    def _add_repo(self, repo):
        dnf_repo = self._base.repos.add_repo(repo.name, repo.baseurl)
        dnf_repo.enable()
        try:
            dnf_repo.get_primary_xml():
        except dnf.RepoError as e:
            raise MetadataError(e.value)

    def __init__(self, data):
        if rpm is None or yum is None:
            raise PayloadError("unsupported payload type")

        PackagePayload.__init__(self, data)
        self._base = dnf.YumBase()

    def setup(self, storage):
        self.updateBaseRepo()
        self.gatherRepoMetadata()

    def reset(self, root=None):
        self._base.drop_sack()

    def release(self):
        self._base.drop_sack()
        self._base.drop_repos()

    @property
    def repos(self):
        # repo ids that DNF can see by itself
        return [r.id for r in self._base.repos.values()]

    @property
    def addOns(self):
        # addon repos via kickstart
        return [r.name for r in self.data.repo.dataList()]

    @property
    def baseRepo(self):
        repo_names = [constants.BASE_REPO_NAME] + default_repos
        for repo_name in repo_names:
            if repo_name in self.repos:
                if self._base.repos[repo_name].enabled:
                    return repo_name

        return None

    def updateBaseRepo(self, fallback=True, root=None, checkmount=True):
        # tbd:
        # 1) configuring the base repo based on self.data.method.method
        # 2) disabling all unwanted repos
        pass

    def gatherRepoMetadata(self):
        map(self._sync_metadata, self._base.repos.values())

    def configureAddOnRepo(self, repo):
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

    # tbd
    # 1) groups manipulation
    # 2) environments manipulation
