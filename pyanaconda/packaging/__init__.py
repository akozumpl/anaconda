# __init__.py
# Entry point for anaconda's software management module.
#
# Copyright (C) 2012  Red Hat, Inc.
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
# Red Hat Author(s): David Lehman <dlehman@redhat.com>
#                    Chris Lumens <clumens@redhat.com>
#

"""
    TODO
        - error handling!!!
        - document all methods

"""

import os
from urlgrabber.grabber import URLGrabber
from urlgrabber.grabber import URLGrabError
import ConfigParser
import shutil
import time

if __name__ == "__main__":
    from pyanaconda import anaconda_log
    anaconda_log.init()

from pyanaconda.constants import *
from pyanaconda.flags import flags

from pyanaconda import iutil
from pyanaconda import isys
from pyanaconda.image import mountImage
from pyanaconda.iutil import ProxyString, ProxyStringError

from pykickstart.parser import Group

import logging
log = logging.getLogger("packaging")

from pyanaconda.errors import *
from blivet.errors import StorageError
import blivet.util
import blivet.arch
from blivet.platform import platform
#from pyanaconda.progress import progress

from pyanaconda.product import productName, productVersion
import urlgrabber
urlgrabber.grabber.default_grabber.opts.user_agent = "%s (anaconda)/%s" %(productName, productVersion)

###
### ERROR HANDLING
###
class PayloadError(Exception):
    pass

class MetadataError(PayloadError):
    pass

class NoNetworkError(PayloadError):
    pass

# setup
class PayloadSetupError(PayloadError):
    pass

class ImageMissingError(PayloadSetupError):
    pass

class ImageDirectoryMountError(PayloadSetupError):
    pass

# software selection
class NoSuchGroup(PayloadError):
    pass

class NoSuchPackage(PayloadError):
    pass

class DependencyError(PayloadError):
    pass

# installation
class PayloadInstallError(PayloadError):
    pass

class Payload(object):
    """ Payload is an abstract class for OS install delivery methods. """
    def __init__(self, data):
        """ data is a kickstart.AnacondaKSHandler class
        """
        self.data = data
        self._kernelVersionList = []
        self._createdInitrds = False
        self.txID = None

    def setup(self, storage):
        """ Do any payload-specific setup. """
        self.storage = storage

    def preStorage(self):
        """ Do any payload-specific work necessary before writing the storage
            configuration.  This method need not be provided by all payloads.
        """
        pass

    def release(self):
        """ Release any resources in use by this object, but do not do final
            cleanup.  This is useful for dealing with payload backends that do
            not get along well with multithreaded programs.
        """
        pass

    def reset(self):
        """ Reset the instance, not including ksdata. """
        pass

    ###
    ### METHODS FOR WORKING WITH REPOSITORIES
    ###
    @property
    def repos(self):
        """A list of repo identifiers, not objects themselves."""
        raise NotImplementedError()

    @property
    def addOns(self):
        """ A list of addon repo identifiers. """
        return [r.name for r in self.data.repo.dataList()]

    @property
    def baseRepo(self):
        """ The identifier of the current base repo. """
        return None

    @property
    def mirrorEnabled(self):
        """Is the closest/fastest mirror option enabled?  This does not make
           sense for those payloads that do not support this concept.
        """
        return True

    def getRepo(self, repo_id):
        """ Return the package repo object. """
        raise NotImplementedError()

    def isRepoEnabled(self, repo_id):
        """ Return True if repo is enabled. """
        repo = self.getAddOnRepo(repo_id)
        if repo:
            return repo.enabled
        else:
            return False

    def getAddOnRepo(self, repo_id):
        """ Return a ksdata Repo instance matching the specified repo id. """
        repo = None
        for r in self.data.repo.dataList():
            if r.name == repo_id:
                repo = r
                break

        return repo

    def _repoNeedsNetwork(self, repo):
        """ Returns True if the ksdata repo requires networking. """
        urls = [repo.baseurl]
        if repo.mirrorlist:
            urls.extend(repo.mirrorlist)
        network_protocols = ["http:", "ftp:", "nfs:", "nfsiso:"]
        for url in urls:
            if any(url.startswith(p) for p in network_protocols):
                return True

        return False

    @property
    def needsNetwork(self):
        return any(self._repoNeedsNetwork(r) for r in self.data.repo.dataList())

    def _resetMethod(self):
        self.data.method.method = ""
        self.data.method.url = None
        self.data.method.server = None
        self.data.method.dir = None
        self.data.method.partition = None
        self.data.method.biospart = None
        self.data.method.noverifyssl = False
        self.data.method.proxy = ""
        self.data.method.opts = None

    def updateBaseRepo(self, fallback=True, root=None, checkmount=True):
        """ Update the base repository from ksdata.method. """
        pass

    def gatherRepoMetadata(self):
        pass

    def addRepo(self, newrepo):
        """Add the repo given by the pykickstart Repo object newrepo to the
           system.  The repo will be automatically enabled and its metadata
           fetched.

           Duplicate repos will not raise an error.  They should just silently
           take the place of the previous value.
        """
        # Add the repo to the ksdata so it'll appear in the output ks file.
        self.data.repo.dataList().append(newrepo)

    def removeRepo(self, repo_id):
        repos = self.data.repo.dataList()
        try:
            idx = [repo.name for repo in repos].index(repo_id)
        except ValueError:
            log.error("failed to remove repo %s: not found" % repo_id)
        else:
            repos.pop(idx)

    def enableRepo(self, repo_id):
        repo = self.getAddOnRepo(repo_id)
        if repo:
            repo.enabled = True

    def disableRepo(self, repo_id):
        repo = self.getAddOnRepo(repo_id)
        if repo:
            repo.enabled = False

    ###
    ### METHODS FOR WORKING WITH ENVIRONMENTS
    ###
    @property
    def environments(self):
        raise NotImplementedError()

    def environmentSelected(self, environmentid):
        raise NotImplementedError()

    def environmentHasOption(self, environmentid, grpid):
        raise NotImplementedError()

    def environmentOptionIsDefault(self, environmentid, grpid):
        raise NotImplementedError()

    def environmentDescription(self, environmentid):
        raise NotImplementedError()

    def selectEnvironment(self, environmentid):
        raise NotImplementedError()

    def deselectEnvironment(self, environmentid):
        raise NotImplementedError()

    def environmentGroups(self, environmentid):
        raise NotImplementedError()

    ###
    ### METHODS FOR WORKING WITH GROUPS
    ###
    @property
    def groups(self):
        raise NotImplementedError()

    def languageGroups(self):
        return []

    def groupDescription(self, groupid):
        raise NotImplementedError()

    def groupSelected(self, groupid):
        return Group(groupid) in self.data.packages.groupList

    def selectGroup(self, groupid, default=True, optional=False):
        if optional:
            include = GROUP_ALL
        elif default:
            include = GROUP_DEFAULT
        else:
            include = GROUP_REQUIRED

        grp = Group(groupid, include=include)

        if grp in self.data.packages.groupList:
            # I'm not sure this would ever happen, but ensure that re-selecting
            # a group with a different types set works as expected.
            if grp.include != include:
                grp.include = include

            return

        if grp in self.data.packages.excludedGroupList:
            self.data.packages.excludedGroupList.remove(grp)

        self.data.packages.groupList.append(grp)

    def deselectGroup(self, groupid):
        grp = Group(groupid)

        if grp in self.data.packages.excludedGroupList:
            return

        if grp in self.data.packages.groupList:
            self.data.packages.groupList.remove(grp)

        self.data.packages.excludedGroupList.append(grp)

    ###
    ### METHODS FOR WORKING WITH PACKAGES
    ###
    @property
    def packages(self):
        raise NotImplementedError()

    def packageSelected(self, pkgid):
        return pkgid in self.data.packages.packageList

    def selectPackage(self, pkgid):
        """Mark a package for installation.

           pkgid - The name of a package to be installed.  This could include
                   a version or architecture component.
        """
        if pkgid in self.data.packages.packageList:
            return

        if pkgid in self.data.packages.excludedList:
            self.data.packages.excludedList.remove(pkgid)

        self.data.packages.packageList.append(pkgid)

    def deselectPackage(self, pkgid):
        """Mark a package to be excluded from installation.

           pkgid - The name of a package to be excluded.  This could include
                   a version or architecture component.
        """
        if pkgid in self.data.packages.excludedList:
            return

        if pkgid in self.data.packages.packageList:
            self.data.packages.packageList.remove(pkgid)

        self.data.packages.excludedList.append(pkgid)

    ###
    ### METHODS FOR QUERYING STATE
    ###
    @property
    def spaceRequired(self):
        """ The total disk space (Size) required for the current selection. """
        raise NotImplementedError()

    @property
    def kernelVersionList(self):
        if not self._kernelVersionList:
            import glob
            try:
                import yum
            except ImportError:
                cmpfunc = cmp
            else:
                cmpfunc = yum.rpmUtils.miscutils.compareVerOnly

            files = glob.glob(ROOT_PATH + "/boot/vmlinuz-*")
            files.extend(glob.glob(ROOT_PATH + "/boot/efi/EFI/redhat/vmlinuz-*"))
            # strip off everything up to and including vmlinuz- to get versions
            # Ignore rescue kernels
            versions = [f.split("/")[-1][8:] for f in files if os.path.isfile(f) \
                        and "-rescue-" not in f]
            versions.sort(cmp=cmpfunc)
            log.debug("kernel versions: %s" % versions)
            self._kernelVersionList = versions

        return self._kernelVersionList

    ##
    ## METHODS FOR TREE VERIFICATION
    ##
    def _getTreeInfo(self, url, proxy_url, sslverify):
        """ Retrieve treeinfo and return the path to the local file.

            :param baseurl: url of the repo
            :type baseurl: string
            :param proxy_url: Optional full proxy URL of or ""
            :type proxy_url: string
            :param sslverify: True if SSL certificate should be varified
            :type sslverify: bool
            :returns: Path to retrieved .treeinfo file or None
            :rtype: string or None
        """
        if not url:
            return None

        log.debug("retrieving treeinfo from %s (proxy: %s ; sslverify: %s)"
                    % (url, proxy_url, sslverify))

        ugopts = {"ssl_verify_peer": sslverify,
                  "ssl_verify_host": sslverify}

        proxies = {}
        if proxy_url:
            try:
                proxy = ProxyString(proxy_url)
                proxies = {"http": proxy.url,
                           "https": proxy.url}
            except ProxyStringError as e:
                log.info("Failed to parse proxy for _getTreeInfo %s: %s" \
                         % (proxy_url, e))

        ug = URLGrabber()
        try:
            treeinfo = ug.urlgrab("%s/.treeinfo" % url,
                                  "/tmp/.treeinfo", copy_local=True,
                                  proxies=proxies, **ugopts)
        except URLGrabError as e:
            try:
                treeinfo = ug.urlgrab("%s/treeinfo" % url,
                                      "/tmp/.treeinfo", copy_local=True,
                                      proxies=proxies, **ugopts)
            except URLGrabError as e:
                log.info("Error downloading treeinfo: %s" % e)
                treeinfo = None

        return treeinfo

    def _getReleaseVersion(self, url):
        """ Return the release version of the tree at the specified URL. """
        version = productVersion.split("-")[0]

        log.debug("getting release version from tree at %s (%s)" % (url,
                                                                    version))

        treeinfo = self._getTreeInfo(url, self.data.method.proxy, not flags.noverifyssl)
        if treeinfo:
            c = ConfigParser.ConfigParser()
            c.read(treeinfo)
            try:
                # Trim off any -Alpha or -Beta
                version = c.get("general", "version").split("-")[0]
            except ConfigParser.Error:
                pass

        if version.startswith(time.strftime("%Y")):
            version = "rawhide"

        log.debug("got a release version of %s" % version)
        return version

    ##
    ## METHODS FOR MEDIA MANAGEMENT (XXX should these go in another module?)
    ##
    @staticmethod
    def _setupDevice(device, mountpoint):
        """ Prepare an install CD/DVD for use as a package source. """
        log.info("setting up device %s and mounting on %s" % (device.name,
                                                              mountpoint))
        # Is there a symlink involved?  If so, let's get the actual path.
        # This is to catch /run/install/isodir vs. /mnt/install/isodir, for
        # instance.
        realMountpoint = os.path.realpath(mountpoint)

        if os.path.ismount(realMountpoint):
            mdev = blivet.util.get_mount_device(realMountpoint)
            if mdev:
                log.warning("%s is already mounted on %s" % (mdev, mountpoint))

            if mdev == device.path:
                return
            else:
                try:
                    blivet.util.umount(realMountpoint)
                except Exception as e:
                    log.error(str(e))
                    log.info("umount failed -- mounting on top of it")

        try:
            device.setup()
            device.format.setup(mountpoint=mountpoint)
        except StorageError as e:
            log.error("mount failed: %s" % e)
            device.teardown(recursive=True)
            raise PayloadSetupError(str(e))

    @staticmethod
    def _setupNFS(mountpoint, server, path, options):
        """ Prepare an NFS directory for use as a package source. """
        log.info("mounting %s:%s:%s on %s" % (server, path, options, mountpoint))
        if os.path.ismount(mountpoint):
            dev = blivet.util.get_mount_device(mountpoint)
            _server, colon, _path = dev.partition(":")
            if colon == ":" and server == _server and path == _path:
                log.debug("%s:%s already mounted on %s" % (server, path,
                                                           mountpoint))
                return
            else:
                log.debug("%s already has something mounted on it" % mountpoint)
                try:
                    blivet.util.umount(mountpoint)
                except OSError as e:
                    log.error(str(e))
                    log.info("umount failed -- mounting on top of it")

        # mount the specified directory
        url = "%s:%s" % (server, path)

        try:
            blivet.util.mount(url, mountpoint, fstype="nfs", options=options)
        except OSError as e:
            raise PayloadSetupError(str(e))

    ###
    ### METHODS FOR INSTALLING THE PAYLOAD
    ###
    def preInstall(self, packages=None, groups=None):
        """ Perform pre-installation tasks. """
        iutil.mkdirChain(ROOT_PATH + "/root")

    def install(self):
        """ Install the payload. """
        raise NotImplementedError()

    def _copyDriverDiskFiles(self):
        import glob
        import shutil

        new_firmware = False

        # Multiple driver disks may be loaded, so we need to glob for all
        # the firmware files in the common DD firmware directory
        for f in glob.glob(DD_FIRMWARE+"/*"):
            try:
                shutil.copyfile(f, "%s/lib/firmware/" % ROOT_PATH)
            except IOError as e:
                log.error("Could not copy firmware file %s: %s" % (f, e.strerror))
            else:
                new_firmware = True

        #copy RPMS
        for d in glob.glob(DD_RPMS):
            shutil.copytree(d, ROOT_PATH + "/root/" + os.path.basename(d))

        #copy modules and firmware into root's home directory
        if os.path.exists(DD_ALL):
            try:
                shutil.copytree(DD_ALL, ROOT_PATH + "/root/DD")
            except IOError as e:
                log.error("failed to copy driver disk files: %s" % e.strerror)
                # XXX TODO: real error handling, as this is probably going to
                #           prevent boot on some systems

        if new_firmware:
            self._recreateInitrds()

    def _recreateInitrds(self, force=False):
        if not force and self._createdInitrds:
            return

        for kernel in self.kernelVersionList:
            log.info("recreating initrd for %s" % kernel)
            iutil.execWithRedirect("new-kernel-pkg",
                                   ["--mkinitrd", "--dracut",
                                    "--depmod", "--install", kernel],
                                   root=ROOT_PATH)
            iutil.execWithRedirect("new-kernel-pkg",
                                   ["--rpmposttrans", kernel],
                                   root=ROOT_PATH)
        self._createdInitrds = True


    def _setDefaultBootTarget(self):
        """ Set the default systemd target for the system. """
        if not os.path.exists(ROOT_PATH + "/etc/systemd/system"):
            log.error("systemd is not installed -- can't set default target")
            return

        # If X was already requested we don't have to continue
        if self.data.xconfig.startX:
            return

        try:
            import rpm
        except ImportError:
            log.info("failed to import rpm -- not adjusting default runlevel")
        else:
            ts = rpm.TransactionSet(ROOT_PATH)

            # XXX one day this might need to account for anaconda's display mode
            if ts.dbMatch("provides", 'service(graphical-login)').count() and \
               ts.dbMatch('provides', 'xorg-x11-server-Xorg').count() and \
               not flags.usevnc:
                # We only manipulate the ksdata.  The symlink is made later
                # during the config write out.
                self.data.xconfig.startX = True

    def dracutSetupArgs(self):
        args = []
        try:
            import rpm
        except ImportError:
            pass
        else:
            iutil.resetRpmDb()
            ts = rpm.TransactionSet(ROOT_PATH)

            # Only add "rhgb quiet" on non-s390, non-serial installs
            if iutil.isConsoleOnVirtualTerminal() and \
               (ts.dbMatch('provides', 'rhgb').count() or \
                ts.dbMatch('provides', 'plymouth').count()):
                args.extend(["rhgb", "quiet"])

        return args

    def postInstall(self):
        """ Perform post-installation tasks. """

        # set default systemd target
        self._setDefaultBootTarget()

        # write out static config (storage, modprobe, keyboard, ??)
        #   kickstart should handle this before we get here

        self._copyDriverDiskFiles()

class ImagePayload(Payload):
    """ An ImagePayload installs an OS image to the target system. """
    pass

class ArchivePayload(ImagePayload):
    """ An ArchivePayload unpacks source archives onto the target system. """
    pass

class PackagePayload(Payload):
    """ A PackagePayload installs a set of packages onto the target system. """
    @property
    def kernelPackages(self):
        kernels = ["kernel"]

        if isys.isPaeAvailable():
            kernels.insert(0, "kernel-PAE")

        # most ARM systems use platform-specific kernels
        if blivet.arch.isARM():
            if platform.armMachine is not None:
                kernels = ["kernel-%s" % platform.armMachine]

        return kernels

    def _setupMedia(self, device):
        method = self.data.method
        if method.method == "harddrive":
            self._setupDevice(device, mountpoint=ISO_DIR)

            # check for ISO images in the newly mounted dir
            path = ISO_DIR
            if method.dir:
                path = os.path.normpath("%s/%s" % (path, method.dir))

            # XXX it would be nice to streamline this when we're just setting
            #     things back up after storage activation instead of having to
            #     pretend we don't already know which ISO image we're going to
            #     use
            image = findFirstIsoImage(path)
            if not image:
                device.teardown(recursive=True)
                raise PayloadSetupError("failed to find valid iso image")

            if path.endswith(".iso"):
                path = os.path.dirname(path)

            # this could already be set up the first time through
            if not os.path.ismount(INSTALL_TREE):
                # mount the ISO on a loop
                image = os.path.normpath("%s/%s" % (path, image))
                mountImage(image, INSTALL_TREE)

            if not method.dir.endswith(".iso"):
                method.dir = os.path.normpath("%s/%s" % (method.dir,
                                                         os.path.basename(image)))
                while method.dir.startswith("/"):
                    # riduculous
                    method.dir = method.dir[1:]
        # Check to see if the device is already mounted, in which case
        # we don't need to mount it again
        elif method.method == "cdrom" and \
             blivet.util.get_mount_paths(device.path):
                return
        else:
            device.format.setup(mountpoint=INSTALL_TREE)

def payloadInitialize(storage, ksdata, payload):
    from pyanaconda.threads import threadMgr

    threadMgr.wait(THREAD_STORAGE)

    # FIXME: condition for cases where we don't want network
    # (set and use payload.needsNetwork ?)
    threadMgr.wait(THREAD_WAIT_FOR_CONNECTING_NM)

    payload.setup(storage)

def show_groups(payload):
    #repo = ksdata.RepoData(name="anaconda", baseurl="http://cannonball/install/rawhide/os/")
    #obj.addRepo(repo)

    desktops = []
    addons = []

    for grp in payload.groups:
        if grp.endswith("-desktop"):
            desktops.append(payload.description(grp))
        elif not grp.endswith("-support"):
            addons.append(payload.description(grp))

    import pprint

    print "==== DESKTOPS ===="
    pprint.pprint(desktops)
    print "==== ADDONS ===="
    pprint.pprint(addons)

    print payload.groups

def print_txmbrs(payload, f=None):
    if f is None:
        f = sys.stdout

    print >> f, "###########"
    for txmbr in payload._yum.tsInfo.getMembers():
        print >> f, txmbr
    print >> f, "###########"

def write_txmbrs(payload, filename):
    if os.path.exists(filename):
        os.unlink(filename)

    f = open(filename, 'w')
    print_txmbrs(payload, f)
    f.close()

###
### MAIN
###
if __name__ == "__main__":
    import os
    import sys
    import blivet
    from pykickstart.version import makeVersion
    from pyanaconda.packaging.yumpayload import YumPayload

    # set some things specially since we're just testing
    flags.testing = True

    # set up ksdata
    ksdata = makeVersion()

    #ksdata.method.method = "url"
    #ksdata.method.url = "http://husky/install/f17/os/"
    #ksdata.method.url = "http://dl.fedoraproject.org/pub/fedora/linux/development/17/x86_64/os/"

    # set up storage
    storage = blivet.Blivet(ksdata=ksdata)
    storage.reset()

    # set up the payload
    payload = YumPayload(ksdata)
    payload.setup(storage)

    for repo in payload._yum.repos.repos.values():
        print repo.name, repo.enabled

    ksdata.method.method = "url"
    #ksdata.method.url = "http://husky/install/f17/os/"
    ksdata.method.url = "http://dl.fedoraproject.org/pub/fedora/linux/development/17/x86_64/os/"

    # now switch the base repo to what we set ksdata.method to just above
    payload.updateBaseRepo()
    for repo in payload._yum.repos.repos.values():
        print repo.name, repo.enabled

    # list all of the groups
    show_groups(payload)
