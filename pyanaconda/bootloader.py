# bootloader.py
# Anaconda's bootloader configuration module.
#
# Copyright (C) 2011 Red Hat, Inc.
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
#

import sys
import os
import re
import struct

import pyanaconda.view
from pyanaconda import iutil
from pyanaconda.storage.devicelibs import mdraid
from pyanaconda.isys import sync
from pyanaconda.product import productName
from pyanaconda.flags import flags
from pyanaconda.constants import *
from pyanaconda.storage.errors import StorageError

import gettext
_ = lambda x: gettext.ldgettext("anaconda", x)
N_ = lambda x: x

import logging
log = logging.getLogger("anaconda")


def get_boot_block(device, seek_blocks=0):
    status = device.status
    if not status:
        try:
            device.setup()
        except StorageError:
            return ""
    block_size = device.partedDevice.sectorSize
    fd = os.open(device.path, os.O_RDONLY)
    if seek_blocks:
        os.lseek(fd, seek_blocks * block_size, 0)
    block = os.read(fd, 512)
    os.close(fd)
    if not status:
        try:
            device.teardown(recursive=True)
        except StorageError:
            pass

    return block

def is_windows_boot_block(block):
    try:
        windows = (len(block) >= 512 and
                   struct.unpack("H", block[0x1fe: 0x200]) == (0xaa55,))
    except struct.error:
        windows = False
    return windows

def has_windows_boot_block(device):
    return is_windows_boot_block(get_boot_block(device))


class BootLoaderError(Exception):
    pass


class ArgumentList(list):
    def _is_match(self, item, value):
        try:
            _item = item.split("=")[0]
        except (ValueError, AttributeError):
            _item = item

        try:
            _value = value.split("=")[0]
        except (ValueError, AttributeError):
            _value = value

        return _item == _value

    def __contains__(self, value):
        for arg in self:
            if self._is_match(arg, value):
                return True

        return False

    def count(self, value):
        return len([x for x in self if self._is_match(x, value)])

    def index(self, value):
        for i in range(len(self)):
            if self._is_match(self[i], value):
                return i

        raise ValueError("'%s' is not in list" % value)

    def rindex(self, value):
        for i in reversed(range(len(self))):
            if self._is_match(self[i], value):
                return i

        raise ValueError("'%s' is not in list" % value)

    def append(self, value):
        """ Append a new argument to the list.

            If the value is an exact match to an existing argument it will
            not be added again. Also, empty strings will not be added.
        """
        if value == "":
            return

        try:
            idx = self.index(value)
        except ValueError:
            pass
        else:
            if self[idx] == value:
                return

        super(ArgumentList, self).append(value)

    def extend(self, values):
        map(self.append, values)

    def __str__(self):
        return " ".join(map(str, self))


class BootLoaderImage(object):
    """ Base class for bootloader images. Suitable for non-linux OS images. """
    def __init__(self, device=None, label=None, short=None):
        self.label = label
        self.short_label = short
        self.device = device


class LinuxBootLoaderImage(BootLoaderImage):
    def __init__(self, device=None, label=None, short=None, version=None):
        super(LinuxBootLoaderImage, self).__init__(device=device, label=label)
        self.label = label              # label string
        self.short_label = short        # shorter label string
        self.device = device            # StorageDevice instance
        self.version = version          # kernel version string
        self._kernel = None             # filename string
        self._initrd = None             # filename string

    @property
    def kernel(self):
        filename = self._kernel
        if self.version and not filename:
            filename = "vmlinuz-%s" % self.version
        return filename

    @property
    def initrd(self):
        filename = self._initrd
        if self.version and not filename:
            filename = "initramfs-%s.img" % self.version
        return filename


class BootLoader(object):
    """TODO:
            - iSeries bootloader?
                - same as pSeries, except optional, I think
            - upgrade of non-grub bootloaders
            - detection of existing bootloaders
            - resolve overlap between Platform checkFoo methods and
              _is_valid_target_device and _is_valid_boot_device
            - split certain parts of _is_valid_target_device and
              _is_valid_boot_device into specific bootloader classes
    """
    name = "Generic Bootloader"
    packages = []
    config_file = None
    config_file_mode = 0600
    can_dual_boot = False
    can_update = False
    image_label_attr = "label"

    # requirements for bootloader target devices
    target_device_types = []
    target_device_raid_levels = []
    target_device_format_types = []
    target_device_disklabel_types = []
    target_device_mountpoints = []
    target_device_min_size = None
    target_device_max_size = None

    # for UI use, eg: "mdarray": N_("RAID Device")
    target_descriptions = {}

    # requirements for boot devices
    boot_device_types = []
    boot_device_raid_levels = []
    boot_device_format_types = ["ext4", "ext3", "ext2"]
    boot_device_mountpoints = ["/boot", "/"]
    boot_device_min_size = 50
    boot_device_max_size = None
    non_linux_boot_device_format_types = []

    # this is so stupid...
    global_preserve_args = ["speakup_synth", "apic", "noapic", "apm", "ide",
                            "noht", "acpi", "video", "pci", "nodmraid",
                            "nompath", "nomodeset", "noiswmd", "fips"]
    preserve_args = []

    def __init__(self, storage=None):
        # pyanaconda.storage.Storage instance
        self.storage = storage

        self.boot_args = ArgumentList()
        self.dracut_args = []

        self._drives = []
        self._drive_order = []

        # timeout in seconds
        self._timeout = None
        self.password = None

        # console/serial stuff
        self.console = ""
        self.console_options = ""
        self._set_console()

        # list of BootLoaderImage instances representing bootable OSs
        self.linux_images = []
        self.chain_images = []

        # default image
        self._default_image = None

        # the device the bootloader will be installed on
        self._target_device = None

        self._update_only = False

    #
    # target device access
    #
    @property
    def stage1_device(self):
        """ Stage1 target device. """
        if not self._target_device:
            self.stage1_device = self.target_devices[0]

        return self._target_device

    @stage1_device.setter
    def stage1_device(self, device):
        if not self._is_valid_target_device(device):
            raise ValueError("%s is not a valid target device" % device.name)

        log.debug("new bootloader stage1 device: %s" % device.name)
        self._target_device = device

    @property
    def stage2_device(self):
        """ Stage2 target device. """
        return self.storage.mountpoints.get("/boot", self.storage.rootDevice)

    #
    # drive list access
    #
    @property
    def drive_order(self):
        """Potentially partial order for drives."""
        return self._drive_order

    @drive_order.setter
    def drive_order(self, order):
        log.debug("new drive order: %s" % order)
        self._drive_order = order
        self.clear_drive_list() # this will get regenerated on next access

    def _sort_drives(self, drives):
        """Sort drives based on the drive order."""
        _drives = drives[:]
        for name in reversed(self._drive_order):
            try:
                idx = [d.name for d in _drives].index(name)
            except ValueError:
                log.error("bios order specified unknown drive %s" % name)
                continue

            first = _drives.pop(idx)
            _drives.insert(0, first)

        return _drives

    def clear_drive_list(self):
        """ Clear the drive list to force re-populate on next access. """
        self._drives = []

    @property
    def drives(self):
        """Sorted list of available drives."""
        if self._drives:
            # only generate the list if it is empty
            return self._drives

        # XXX requiring partitioned may break clearpart
        drives = [d for d in self.storage.disks if d.partitioned]
        self._drives = self._sort_drives(drives)
        return self._drives

    #
    # image list access
    #
    @property
    def default(self):
        """The default image."""
        if not self._default_image:
            if self.linux_images:
                _default = self.linux_images[0]
            else:
                _default = LinuxBootLoaderImage(device=self.storage.rootDevice,
                                                label=productName,
                                                short="linux")

            self._default_image = _default

        return self._default_image

    @default.setter
    def default(self, image):
        if image not in self.images:
            raise ValueError("new default image not in image list")

        log.debug("new default image: %s" % image)
        self._default_image = image

    @property
    def images(self):
        """ List of OS images that will be included in the configuration. """
        if not self.linux_images:
            self.linux_images.append(self.default)

        all_images = self.linux_images
        all_images.extend([i for i in self.chain_images if i.label])
        return all_images

    def clear_images(self):
        """Empty out the image list."""
        self.linux_images = []
        self.chain_images = []

    def add_image(self, image):
        """Add a BootLoaderImage instance to the image list."""
        if isinstance(image, LinuxBootLoaderImage):
            self.linux_images.append(image)
        else:
            self.chain_images.append(image)

    def image_label(self, image):
        """Return the appropriate image label for this bootloader."""
        return getattr(image, self.image_label_attr)

    def _find_chain_images(self):
        """ Collect a list of potential non-linux OS installations. """
        # XXX not used -- do we want to pre-populate the image list for the ui?
        self.chain_images = []
        if not self.can_dual_boot:
            return

        for device in [d for d in self.bootable_chain_devices if d.exists]:
            self.chain_images.append(BootLoaderImage(device=device))

    #
    # target/stage1 device access
    #
    def _device_type_index(self, device, types):
        """ Return the index of the matching type in types to device's type.

            Return None if no match is found. """
        index = None
        try:
            index = types.index(device.type)
        except ValueError:
            if "disk" in types and device.isDisk:
                index = types.index("disk")

        return index

    def _device_type_match(self, device, types):
        """ Return True if device is of one of the types in the list types. """
        return self._device_type_index(device, types) is not None

    def device_description(self, device):
        idx = self._device_type_index(device, self.target_device_types)
        if idx is None:
            raise ValueError("'%s' not a valid stage1 type" % device.type)

        return self.target_descriptions[self.target_device_types[idx]]

    def set_preferred_stage2_type(self, preferred):
        """ Set a preferred type of stage1 device.

            XXX should this reorder the list or remove everything else? """
        if preferred == "mbr":
            preferred = "disk"

        try:
            index = self.target_device_types.index(preferred)
        except ValueError:
            raise ValueError("'%s' not a valid stage1 device type" % preferred)

        self.target_device_types.insert(0, self.target_device_types.pop(index))

    def _is_valid_target_device(self, device):
        """ Return True if the device is a valid stage1 target device.

            The criteria for being a valid stage1 target device vary from
            platform to platform. On some platforms a disk with an msdos
            disklabel is a valid stage1 target, while some platforms require
            a special device. Some examples of these special devices are EFI
            system partitions on EFI machines, PReP boot partitions on
            iSeries, and Apple bootstrap partitions on Mac. """
        if not self._device_type_match(device, self.target_device_types):
            return False

        if (self.target_device_min_size is not None and
            device.size < self.target_device_min_size):
            return False

        if (self.target_device_max_size is not None and
            device.size > self.target_device_max_size):
            return False

        if not getattr(device, "bootable", True) or \
           (hasattr(device, "partedPartition") and
            not device.partedPartition.active):
            return False

        if getattr(device.format, "label", None) == "ANACONDA":
            return False

        if self.target_device_format_types and \
           device.format.type not in self.target_device_format_types:
            return False

        if self.target_device_disklabel_types:
            for disk in device.disks:
                label_type = disk.format.labelType
                if label_type not in self.target_device_disklabel_types:
                    return False

        if self.target_device_mountpoints and \
           hasattr(device.format, "mountpoint") and \
           device.format.mountpoint not in self.target_device_mountpoints:
            return False

        return True

    @property
    def target_devices(self):
        """ A list of valid stage1 target devices.

            The list self.target_device_types is ordered, so we return a list
            of all valid target devices, sorted by device type, then sorted
            according to our drive ordering.
        """
        slots = [[] for t in self.target_device_types]
        for device in self.storage.devices:
            idx = self._device_type_index(device, self.target_device_types)
            if idx is None:
                continue

            if self._is_valid_target_device(device):
                slots[idx].append(device)

        devices = []
        for slot in slots:
            devices.extend(slot)

        return self._sort_drives(devices)

    #
    # boot/stage2 device access
    #

    def _is_valid_boot_device(self, device, linux=True, non_linux=False):
        """ Return True if the specified device might contain an OS image. """
        if not self._device_type_match(device, self.boot_device_types):
            return False

        if device.type == "mdarray" and \
           device.level not in self.boot_device_raid_levels:
            # TODO: also check metadata version, as ridiculous as that is
            return False

        if not self.target_devices:
            # XXX is this really a dealbreaker?
            return False

        # FIXME: the windows boot block part belongs in GRUB
        if hasattr(device, "partedPartition") and \
           (not device.bootable or not device.partedPartition.active) and \
           not has_windows_boot_block(device):
            return False

        format_types = []
        if linux:
            format_types = self.boot_device_format_types
            mountpoint = getattr(device.format, "mountpoint", None)
            if self.boot_device_mountpoints and \
               mountpoint not in self.boot_device_mountpoints:
                return False

        if non_linux:
            format_types.extend(self.non_linux_boot_device_format_types)

        return device.format.type in format_types

    @property
    def bootable_chain_devices(self):
        """ Potential boot devices containing non-linux operating systems. """
        return [d for d in self.storage.devices
                if self._is_valid_boot_device(d, linux=False, non_linux=True)]

    @property
    def bootable_devices(self):
        """ Potential boot devices containing linux operating systems. """
        return [d for d in self.storage.devices
                    if self._is_valid_boot_device(d)]

    #
    # miscellaneous
    #

    @property
    def has_windows(self):
        return False

    @property
    def timeout(self):
        """Bootloader timeout in seconds."""
        if self._timeout is not None:
            t = self._timeout
        elif self.console and self.console.startswith("ttyS"):
            t = 5
        else:
            t = 20

        return t

    @timeout.setter
    def timeout(self, seconds):
        self._timeout = seconds

    @property
    def update_only(self):
        return self._update_only

    @update_only.setter
    def update_only(self, value):
        if value and not self.can_update:
            raise ValueError("this bootloader does not support updates")
        elif self.can_update:
            self._update_only = value

    def set_boot_args(self, *args, **kwargs):
        """ Set up the boot command line.

            Keyword Arguments:

                network - a pyanaconda.network.Network instance (for network
                          storage devices' boot arguments)

            All other arguments are expected to have a dracutSetupString()
            method.
        """
        network = kwargs.pop("network", None)

        #
        # FIPS
        #
        if flags.cmdline.get("fips") == "1":
            self.boot_args.append("boot=%s" % self.stage2_device.fstabSpec)

        #
        # dracut
        #

        # storage
        from pyanaconda.storage.devices import NetworkStorageDevice
        dracut_devices = [self.storage.rootDevice]
        if self.stage2_device != self.storage.rootDevice:
            dracut_devices.append(self.stage2_device)

        dracut_devices.extend(self.storage.fsset.swapDevices)

        done = []
        # When we see a device whose setup string starts with a key in this
        # dict we pop that pair from the dict. When we're done looking at
        # devices we are left with the values that belong in the boot args.
        dracut_storage = {"rd_LUKS_UUID": "rd_NO_LUKS",
                          "rd_LVM_LV": "rd_NO_LVM",
                          "rd_MD_UUID": "rd_NO_MD",
                          "rd_DM_UUID": "rd_NO_DM"}
        for device in dracut_devices:
            for dep in self.storage.devices:
                if device in done:
                    continue

                if device != dep and not device.dependsOn(dep):
                    continue

                setup_string = dep.dracutSetupString().strip()
                if not setup_string:
                    continue

                self.boot_args.append(setup_string)
                self.dracut_args.append(setup_string)
                done.append(dep)
                dracut_storage.pop(setup_string.split("=")[0], None)

                # network storage
                # XXX this is nothing to be proud of
                if isinstance(dep, NetworkStorageDevice):
                    if network is None:
                        log.error("missing network instance for setup of boot "
                                  "command line for network storage device %s"
                                  % dep.name)
                        raise BootLoaderError("missing network instance when "
                                              "setting boot args for network "
                                              "storage device")

                    setup_string = network.dracutSetupString(dep).strip()
                    self.boot_args.append(setup_string)
                    self.dracut_args.append(setup_string)

        self.boot_args.extend(dracut_storage.values())
        self.dracut_args.extend(dracut_storage.values())

        # passed-in objects
        for cfg_obj in list(args) + kwargs.values():
            setup_string = cfg_obj.dracutSetupString().strip()
            self.boot_args.append(setup_string)
            self.dracut_args.append(setup_string)

        #
        # preservation of some of our boot args
        # FIXME: this is stupid.
        #
        for opt in self.global_preserve_args + self.preserve_args:
            if opt not in flags.cmdline:
                continue

            arg = flags.cmdline.get(opt)
            new_arg = opt
            if arg:
                new_arg += "=%s" % arg

            self.boot_args.append(new_arg)

    #
    # configuration
    #

    @property
    def boot_prefix(self):
        """ Prefix, if any, to paths in /boot. """
        if self.stage2_device == self.storage.rootDevice:
            prefix = "/boot"
        else:
            prefix = ""

        return prefix

    def _set_console(self):
        """ Set console options based on boot arguments. """
        if flags.serial:
            console = flags.cmdline.get("console", "ttyS0").split(",", 1)
            self.console = console[0]
            if len(console) > 1:
                self.console_options = console[1]
        elif flags.virtpconsole:
            self.console = re.sub("^/dev/", "", flags.virtpconsole)

    def write_config_console(self, config):
        """Write console-related configuration lines."""
        pass

    def write_config_password(self, config):
        """Write password-related configuration lines."""
        pass

    def write_config_header(self, config):
        """Write global configuration lines."""
        self.write_config_console(config)
        self.write_config_password(config)

    def write_config_images(self, config):
        """Write image configuration entries."""
        # XXX might this be identical for yaboot and silo?
        raise NotImplementedError()

    def write_config_post(self, install_root=""):
        try:
            os.chmod(install_root + self.config_file, self.config_file_mode)
        except OSError as e:
            log.error("failed to set config file permissions: %s" % e)

    def write_config(self, install_root=""):
        """ Write the bootloader configuration. """
        if not self.config_file:
            raise BootLoaderError("no config file defined for this bootloader")

        config_path = os.path.normpath(install_root + self.config_file)
        if os.access(config_path, os.R_OK):
            os.rename(config_path, config_path + ".anacbak")

        config = open(config_path, "w")
        self.write_config_header(config, install_root=install_root)
        self.write_config_images(config)
        config.close()
        self.write_config_post(install_root=install_root)

    def writeKS(self, f):
        """ Write bootloader section of kickstart configuration. """
        if self.stage1_device.isDisk:
            location = "mbr"
        elif self.stage1_device:
            location = "partition"
        else:
            location = "none\n"

        f.write("bootloader --location=%s" % location)

        if not self.stage1_device:
            return

        if self.drive_order:
            f.write(" --driveorder=%s" % ",".join(self.drive_order))

        append = [a for a in self.boot_args if a not in self.dracut_args]
        if append:
            f.write(" --append=\"%s\"" % " ".join(append))

        f.write("\n")

    def read(self):
        """ Read an existing bootloader configuration. """
        raise NotImplementedError()

    #
    # installation
    #
    def write(self, install_root=""):
        """ Write the bootloader configuration and install the bootloader. """
        if self.update_only:
            self.update(install_root=install_root)
            return

        self.write_config(install_root=install_root)
        sync()
        self.stage2_device.format.sync(root=install_root)
        self.install(install_root=install_root)

    def update(self, install_root=""):
        """ Update an existing bootloader configuration. """
        pass


class GRUB(BootLoader):
    name = "GRUB"
    _config_dir = "grub"
    _config_file = "grub.conf"
    _device_map_file = "device.map"
    can_dual_boot = True
    can_update = True

    # list of strings representing options for bootloader target device types
    target_device_types = ["disk", "partition", "mdarray"]
    target_device_raid_levels = [mdraid.RAID1]
    target_device_format_types = []
    target_device_format_mountpoints = ["/boot", "/"]
    target_device_disklabel_types = ["msdos", "gpt"]    # gpt?

    # XXX account for disklabel type since mbr means nothing on gpt
    target_descriptions = {"disk": N_("Master Boot Record"),
                           "partition": N_("First sector of boot partition"),
                           "mdarray": N_("RAID Device")}

    # list of strings representing options for boot device types
    boot_device_types = ["partition", "mdarray"]
    boot_device_raid_levels = [mdraid.RAID1]

    # XXX hpfs, if reported by blkid/udev, will end up with a type of None
    non_linux_device_format_types = ["vfat", "ntfs", "hpfs"]

    packages = ["grub"]

    def __init__(self, storage):
        super(GRUB, self).__init__(storage)
        self.encrypt_password = False

    #
    # grub-related conveniences
    #

    def grub_device_name(self, device):
        """ Return a grub-friendly representation of device. """
        drive = getattr(device, "disk", device)
        name = "(hd%d" % self.drives.index(drive)
        if hasattr(device, "disk"):
            name += ",%d" % (device.partedPartition.number - 1,)
        name += ")"
        return name

    @property
    def grub_config_dir(self):
        """ Config dir, adjusted for grub's view of the world. """
        return self.boot_prefix + self._config_dir

    #
    # configuration
    #

    @property
    def config_dir(self):
        """ Full path to configuration directory. """
        return "/boot/" + self._config_dir

    @property
    def config_file(self):
        """ Full path to configuration file. """
        return "%s/%s" % (self.config_dir, self._config_file)

    @property
    def device_map_file(self):
        """ Full path to device.map file. """
        return "%s/%s" % (self.config_dir, self._device_map_file)

    @property
    def grub_conf_device_line(self):
        return ""

    def write_config_console(self, config):
        """ Write console-related configuration. """
        if not self.console:
            return

        if self.console.startswith("ttyS"):
            unit = self.console[-1]
            speed = "9600"
            for opt in self.console_options.split(","):
                if opt.isdigit:
                    speed = opt
                    break

            config.write("serial --unit=%s --speed=%s\n" % (unit, speed))
            config.write("terminal --timeout=%s serial console\n"
                         % self.timeout)

        console_arg = "console=%s" % self.console
        if self.console_options:
            console_arg += ",%s" % self.console_options
        self.boot_args.append(console_arg)

    @property
    def encrypted_password(self):
        import string
        import crypt
        import random
        salt = "$6$"
        salt_len = 16
        salt_chars = string.letters + string.digits + './'

        rand_gen = random.SystemRandom()
        salt += "".join([rand_gen.choice(salt_chars) for i in range(salt_len)])
        password = crypt.crypt(self.password, salt)
        return password

    def write_config_password(self, config):
        """ Write password-related configuration. """
        if self.password:
            if self.encrypt_password:
                password = "--encrypted " + self.encrypted_password
            else:
                password = self.password

            config.write("password %s\n" % password)

    def write_config_header(self, config, install_root=""):
        """Write global configuration information. """
        if self.boot_prefix:
            have_boot = "do not "
        else:
            have_boot = ""

        s = """# grub.conf generated by anaconda\n"
# Note that you do not have to rerun grub after making changes to this file.
# NOTICE:  You %(do)shave a /boot partition. This means that all kernel and
#          initrd paths are relative to %(boot)s, eg.
#          root %(grub_target)s
#          kernel %(prefix)s/vmlinuz-version ro root=%(root_device)s
#          initrd %(prefix)s/initrd-[generic-]version.img
""" % {"do": have_boot, "boot": self.stage2_device.format.mountpoint,
       "root_device": self.stage2_device.path,
       "grub_target": self.grub_device_name(self.stage1_device),
       "prefix": self.boot_prefix}

        config.write(s)
        config.write("boot=%s\n" % self.stage1_device.path)
        config.write(self.grub_conf_device_line)

        # find the index of the default image
        try:
            default_index = self.images.index(self.default)
        except ValueError:
            e = "Failed to find default image (%s)" % self.default.label
            raise BootLoaderError(e)

        config.write("default=%d\n" % default_index)
        config.write("timeout=%d\n" % self.timeout)

        self.write_config_console(config)

        if not flags.serial:
            splash = "splash.xpm.gz"
            splash_path = os.path.normpath("%s%s/%s" % (install_root,
                                                        self.config_dir,
                                                        splash))
            if os.access(splash_path, os.R_OK):
                grub_root_grub_name = self.grub_device_name(self.stage2_device)
                config.write("splashimage=%s/%s/%s\n" % (grub_root_grub_name,
                                                         self.grub_config_dir,
                                                         splash))
                config.write("hiddenmenu\n")

        self.write_config_password(config)

    def write_config_images(self, config):
        """ Write image entries into configuration file. """
        for image in self.images:
            if isinstance(image, LinuxBootLoaderImage):
                args = ArgumentList()
                grub_root = self.grub_device_name(self.stage2_device)
                args.extend(["ro", "root=%s" % image.device.fstabSpec])
                args.extend(self.boot_args)
                stanza = ("title %(label)s (%(version)s)\n"
                          "\troot %(grub_root)s\n"
                          "\tkernel %(prefix)s/%(kernel)s %(args)s\n"
                          "\tinitrd %(prefix)s/%(initrd)s\n"
                          % {"label": image.label, "version": image.version,
                             "grub_root": grub_root,
                             "kernel": image.kernel, "initrd": image.initrd,
                             "args": args,
                             "prefix": self.boot_prefix})
            else:
                stanza = ("title %(label)s\n"
                          "\trootnoverify %(grub_root)s\n"
                          "\tchainloader +1\n"
                          % {"label": image.label,
                             "grub_root": self.grub_device_name(image.device)})

            config.write(stanza)

    def write_device_map(self, install_root=""):
        """ Write out a device map containing all supported devices. """
        map_path = os.path.normpath(install_root + self.device_map_file)
        if os.access(map_path, os.R_OK):
            os.rename(map_path, map_path + ".anacbak")

        dev_map = open(map_path, "w")
        dev_map.write("# this device map was generated by anaconda\n")
        for drive in self.drives:
            dev_map.write("%s      %s\n" % (self.grub_device_name(drive),
                                            drive.path))
        dev_map.close()

    def write_config_post(self, install_root=""):
        """ Perform additional configuration after writing config file(s). """
        super(GRUB, self).write_config_post(install_root=install_root)

        # make symlink for menu.lst (grub's default config file name)
        menu_lst = "%s%s/menu.lst" % (install_root, self.config_dir)
        if os.access(menu_lst, os.R_OK):
            try:
                os.rename(menu_lst, menu_lst + '.anacbak')
            except OSError as e:
                log.error("failed to back up %s: %s" % (menu_lst, e))

        try:
            os.symlink(self._config_file, menu_lst)
        except OSError as e:
            log.error("failed to create grub menu.lst symlink: %s" % e)

        # make symlink to grub.conf in /etc since that's where configs belong
        etc_grub = "%s/etc/%s" % (install_root, self._config_file)
        if os.access(etc_grub, os.R_OK):
            try:
                os.unlink(etc_grub)
            except OSError as e:
                log.error("failed to remove %s: %s" % (etc_grub, e))

        try:
            os.symlink("..%s" % self.config_file, etc_grub)
        except OSError as e:
            log.error("failed to create /etc/grub.conf symlink: %s" % e)

    def write_config(self, install_root=""):
        """ Write bootloader configuration to disk. """
        # write device.map
        self.write_device_map(install_root=install_root)

        # this writes the actual configuration file
        super(GRUB, self).write_config(install_root=install_root)

    #
    # installation
    #

    def install(self, install_root=""):
        rc = iutil.execWithRedirect("grub-install", ["--just-copy"],
                                    stdout="/dev/tty5", stderr="/dev/tty5",
                                    root=install_root)
        if rc:
            raise BootLoaderError("bootloader install failed")

        boot = self.stage2_device
        targets = []
        if boot.type != "mdarray":
            targets.append((self.stage1_device, self.stage2_device))
        else:
            if [d for d in self.stage2_device.parents if d.type != "partition"]:
                raise BootLoaderError("boot array member devices must be "
                                      "partitions")

            # make sure we have stage1 and stage2 installed with redundancy
            # so that boot can succeed even in the event of failure or removal
            # of some of the disks containing the member partitions of the
            # /boot array
            for stage2dev in self.stage2_device.parents:
                if self.stage1_device.isDisk:
                    # install to mbr
                    if self.stage2_device.dependsOn(self.stage1_device):
                        # if target disk contains any of /boot array's member
                        # partitions, set up stage1 on each member's disk
                        # and stage2 on each member partition
                        stage1dev = stage2dev.disk
                    else:
                        # if target disk does not contain any of /boot array's
                        # member partitions, install stage1 to the target disk
                        # and stage2 to each of the member partitions
                        stage1dev = self.stage1_device
                else:
                    # target is /boot device and /boot is raid, so install
                    # grub to each of /boot member partitions
                    stage1dev = stage2dev

                targets.append((stage1dev, stage2dev))

        for (stage1dev, stage2dev) in targets:
            cmd = ("root %(stage2dev)s\n"
                   "install --stage2=%(config_dir)s/stage2"
                   " /%(grub_config_dir)s/stage1 d %(stage1dev)s"
                   " /%(grub_config_dir)s/stage2 p"
                   " %(stage2dev)s/%(grub_config_dir)s/%(config_basename)s\n"
                   % {"grub_config_dir": self.grub_config_dir,
                      "config_dir": self.config_dir,
                      "config_basename": self._config_file,
                      "stage1dev": self.grub_device_name(self.stage1_device),
                      "stage2dev": self.grub_device_name(self.stage2_device)})
            (pread, pwrite) = os.pipe()
            os.write(pwrite, cmd)
            os.close(pwrite)
            args = ["--batch", "--no-floppy",
                    "--device-map=%s" % self.device_map_file]
            rc = iutil.execWithRedirect("grub", args,
                                        stdout="/dev/tty5", stderr="/dev/tty5",
                                        stdin=pread, root=install_root)
            os.close(pread)
            if rc:
                raise BootLoaderError("bootloader install failed")

    def update(self, install_root=""):
        self.install(install_root=install_root)

    #
    # miscellaneous
    #

    @property
    def has_windows(self):
        return len(self.bootable_chain_devices) != 0


class EFIGRUB(GRUB):
    name = "GRUB (EFI)"
    can_dual_boot = False
    _config_dir = "efi/EFI/redhat"

    # bootloader target device types
    target_device_types = ["partition", "mdarray"]
    target_device_raid_levels = [mdraid.RAID1]
    target_device_format_types = ["efi"]
    target_device_mountpoints = ["/boot/efi"]
    target_device_disklabel_types = ["gpt"]
    target_device_min_size = 50
    target_device_max_size = 256

    target_descriptions = {"partition": N_("EFI System Partition"),
                           "mdarray": N_("RAID Device")}

    non_linux_boot_device_format_types = []

    def efibootmgr(self, *args, **kwargs):
        if kwargs.pop("capture", False):
            exec_func = iutil.execWithCapture
        else:
            exec_func = iutil.execWithRedirect

        return exec_func("efibootmgr", list(args), **kwargs)

    #
    # configuration
    #

    @property
    def efi_product_path(self):
        """ The EFI product path.

            eg: HD(1,800,64000,faacb4ef-e361-455e-bd97-ca33632550c3)
        """
        path = ""
        buf = self.efibootmgr("-v", stderr="/dev/tty5", capture=True)
        matches = re.search(productName + r'\s+HD\(.+?\)', buf)
        if matches:
            path = re.sub(productName + r'\s+',
                          '',
                          buf[matches[0].start():matches[0].end()])

        return path

    @property
    def grub_conf_device_line(self):
        return "device %s %s\n" % (self.grub_device_name(self.stage2_device),
                                   self.efi_product_path)

    #
    # installation
    #

    def remove_efi_boot_target(self, install_root=""):
        buf = self.efibootmgr(capture=True)
        for line in buf.splitlines():
            try:
                (slot, _product) = line.split(None, 1)
            except ValueError:
                continue

            if _product == productName:
                slot_id = slot[4:8]
                if not slot_id.isdigit():
                    log.warning("failed to parse efi boot slot (%s)" % slot)
                    continue

                rc = self.efibootmgr("-b", slot_id, "-B",
                                     root=install_root,
                                     stdout="/dev/tty5", stderr="/dev/tty5")
                if rc:
                    raise BootLoaderError("failed to remove old efi boot entry")

    def add_efi_boot_target(self, install_root=""):
        boot_efi = self.storage.mountpoints["/boot/efi"]
        if boot_efi.type == "partition":
            boot_disk = boot_efi.disk
            boot_part_num = boot_efi.partedPartition.number
        elif boot_efi.type == "mdarray":
            # FIXME: I'm just guessing here. This probably needs the full
            #        treatment, ie: multiple targets for each member.
            boot_disk = boot_efi.parents[0].disk
            boot_part_num = boot_efi.parents[0].partedPartition.number

        rc = self.efibootmgr("-c", "-w", "-L", productName,
                             "-d", boot_disk.path, "-p", boot_part_num,
                             "-l", "\\EFI\\redhat\\grub.efi",
                             root=install_root,
                             stdout="/dev/tty5", stderr="/dev/tty5")
        if rc:
            raise BootLoaderError("failed to set new efi boot target")

    def install(self, install_root=""):
        self.remove_efi_boot_target(install_root=install_root)
        self.add_efi_boot_target(install_root=install_root)

    def update(self, install_root=""):
        self.write(install_root=install_root)


class YabootSILOBase(BootLoader):
    def write_config_password(self, config):
        if self.password:
            config.write("password=%s\n" % self.password)
            config.write("restricted\n")

    def write_config_images(self, config):
        for image in self.images:
            if not isinstance(image, LinuxBootLoaderImage):
                # mac os images are handled specially in the header on mac
                continue

            args = ArgumentList()
            if image.initrd:
                initrd_line = "\tinitrd=%s/%s\n" % (self.boot_prefix,
                                                    image.initrd)
            else:
                initrd_line = ""

            root_device_spec = self.storage.rootDevice.fstabSpec
            if root_device_spec.startswith("/"):
                root_line = "\troot=%s\n" % root_device_spec
            else:
                args.append("root=%s" % root_device_spec)
                root_line = ""

            args.extend(self.boot_args)

            stanza = ("image=%(boot_prefix)s%(kernel)s\n"
                      "\tlabel=%(label)s\n"
                      "\tread-only\n"
                      "%(initrd_line)s"
                      "%(root_line)s"
                      "\tappend=\"%(args)s\"\n\n"
                      % {"kernel": image.kernel,  "initrd_line": initrd_line,
                         "label": self.image_label(image),
                         "root_line": root_line, "args": args,
                         "boot_prefix": self.boot_prefix})
            config.write(stanza)


class Yaboot(YabootSILOBase):
    name = "Yaboot"
    _config_file = "yaboot.conf"
    prog = "ybin"
    image_label_attr = "short_label"
    packages = ["yaboot"]

    # list of strings representing options for bootloader target device types
    target_device_types = ["partition", "mdarray"]
    target_device_raid_levels = [mdraid.RAID1]
    target_device_format_types = ["appleboot", "prepboot"]

    # boot device requirements
    boot_device_types = ["partition", "mdarray"]
    boot_device_raid_levels = [mdraid.RAID1]
    non_linux_boot_device_format_types = ["hfs", "hfs+"]

    def __init__(self, storage):
        BootLoader.__init__(self, storage)

    #
    # configuration
    #

    @property
    def config_dir(self):
        conf_dir = "/etc"
        if self.stage2_device.format.mountpoint == "/boot":
            conf_dir = "/boot/etc"
        return conf_dir

    @property
    def config_file(self):
        return "%s/%s" % (self.config_dir, self._config_file)

    def write_config_header(self, config):
        if self.stage2_device.type == "mdarray":
            boot_part_num = self.stage2_device.parents[0].partedPartition.number
        else:
            boot_part_num = self.stage2_device.partedPartition.number

        # yaboot.conf timeout is in tenths of a second. Brilliant.
        header = ("# yaboot.conf generated by anaconda\n\n"
                  "boot=%(stage1dev)s\n"
                  "init-message=\"Welcome to %(product)s!\\nHit <TAB> for "
                  "boot options\"\n\n"
                  "partition=%(part_num)d\n"
                  "timeout=%(timeout)d\n"
                  "install=/usr/lib/yaboot/yaboot\n"
                  "delay=5\n"
                  "enablecdboot\n"
                  "enableofboot\n"
                  "enablenetboot\n"
                  % {"stage1dev": self.stage1_device.path,
                     "product": productName, "part_num": boot_part_num,
                     "timeout": self.timeout * 10})
        config.write(header)
        self.write_config_variant_header(config)
        self.write_config_password(config)
        config.write("\n")

    def write_config_variant_header(self, config):
        config.write("nonvram\n")
        config.write("mntpoint=/boot/yaboot\n")
        config.write("usemount\n")

    def write_config_post(self, install_root=""):
        super(Yaboot, self).write_config_post(install_root=install_root)

        # make symlink in /etc to yaboot.conf if config is in /boot/etc
        etc_yaboot_conf = install_root + "/etc/yaboot.conf"
        if not os.access(etc_yaboot_conf, os.R_OK):
            try:
                os.symlink("../boot/etc/yaboot.conf", etc_yaboot_conf)
            except OSError as e:
                log.error("failed to create /etc/yaboot.conf symlink: %s" % e)

    def write_config(self, install_root=""):
        if not os.path.isdir(install_root + self.config_dir):
            os.mkdir(install_root + self.config_dir)

        # this writes the config
        super(Yaboot, self).write_config(install_root=install_root)

    #
    # installation
    #

    def install(self, install_root=""):
        args = ["-f", "-C", self.config_file]
        rc = iutil.execWithRedirect(self.prog, args,
                                    stdout="/dev/tty5", stderr="/dev/tty5",
                                    root=install_root)
        if rc:
            raise BootLoaderError("bootloader installation failed")


class IPSeriesYaboot(Yaboot):
    # XXX is this just for pSeries?
    name = "Yaboot (IPSeries)"
    prog = "mkofboot"

    target_device_format_types = ["prepboot"]
    target_device_disklabel_types = ["msdos"]
    target_device_min_size = 4
    target_device_max_size = 10

    target_descriptions = {"partition": N_("PReP Boot Partition"),
                           "mdarray": N_("RAID Device")}

    #
    # configuration
    #

    def write_config_variant_header(self, config):
        config.write("nonvram\n")   # only on pSeries?
        config.write("fstype=raw\n")


class MacYaboot(Yaboot):
    name = "Yaboot (Mac)"
    prog = "mkofboot"

    can_dual_boot = True
    target_device_format_types = ["appleboot"]
    target_device_disklabel_types = ["mac"]
    target_device_min_size = 800.00 / 1024.00
    target_device_max_size = 1

    target_descriptions = {"partition": N_("Apple Bootstrap Partition"),
                           "mdarray": N_("RAID Device")}

    #
    # configuration
    #

    def write_config_variant_header(self, config):
        try:
            mac_os = [i for i in self.chain_images if i.label][0]
        except IndexError:
            pass
        else:
            config.write("macosx=%s\n" % mac_os.device.path)

        config.write("magicboot=/usr/lib/yaboot/ofboot\n")


class ZIPL(BootLoader):
    name = "ZIPL"
    config_file = "/etc/zipl.conf"

    # list of strings representing options for bootloader target device types
    target_device_types = ["disk", "partition"]
    target_device_disklabel_types = ["msdos", "dasd"]

    # list of strings representing options for boot device types
    boot_device_types = ["partition", "mdarray", "lvmlv"]
    boot_device_raid_levels = [mdraid.RAID1]

    packages = ["s390utils"]    # is this bootloader or platform?
    image_label_attr = "short_label"
    preserve_args = ["cio_ignore"]

    #
    # configuration
    #

    @property
    def boot_dir(self):
        return "/boot"

    def write_config_images(self, config):
        for image in self.images:
            args = ArgumentList()
            if image.initrd:
                initrd_line = "\tramdisk=%s/%s\n" % (self.boot_dir,
                                                     image.initrd)
            else:
                initrd_line = ""
            args.append("root=%s/%s" % (self.boot_dir, image.kernel))
            args.extend(self.boot_args)
            stanza = ("[%(label)s]\n"
                      "\timage=%(boot_dir)s/%(kernel)s\n"
                      "%(initrd_line)s"
                      "\tparameters=\"%(args)s\"\n"
                      % {"label": self.image_label(image),
                         "kernel": image.kernel, "initrd_line": initrd_line,
                         "args": args,
                         "boot_dir": self.boot_dir})
            config.write(stanza)

    def write_config_header(self, config):
        header = ("[defaultboot]\n"
                  "timeout=%{timeout}d\n"
                  "default=%{default}\n"
                  "target=/boot\n"
                  % {"timeout": self.timeout,
                     "default": self.image_label(self.default)})
        config.write(header)

    #
    # installation
    #

    def install(self, install_root=""):
        buf = iutil.execWithCapture("zipl", [],
                                    stderr="/dev/tty5",
                                    root=install_root)
        for line in buf.splitlines():
            if line.startswith("Preparing boot device: "):
                # Output here may look like:
                #     Preparing boot device: dasdb (0200).
                #     Preparing boot device: dasdl.
                # We want to extract the device name and pass that.
                name = re.sub(".+?: ", "", line)
                name = re.sub("(\s\(.+\))?\.$", "", name)
                device = self.storage.devicetree.getDeviceByName(name)
                if not device:
                    raise BootLoaderError("could not find IPL device")

                self.stage1_device = device


class SILO(YabootSILOBase):
    name = "SILO"
    _config_file = "silo.conf"
    message_file = "/etc/silo.message"

    # list of strings representing options for bootloader target device types
    target_device_types = ["partition"]
    target_device_disklabel_types = ["sun"]

    # list of strings representing options for boot device types
    boot_device_types = ["partition"]

    packages = ["silo"]

    image_label_attr = "short_label"

    #
    # configuration
    #

    @property
    def config_dir(self):
        if self.stage2_device.format.mountpoint == "/boot":
            return "/boot"
        else:
            return "/etc"

    @property
    def config_file(self):
        return "%s/%s" % (self.config_dir, self._config_file)

    def write_message_file(self, install_root=""):
        message_file = os.path.normpath(install_root + self.message_file)
        f = open(message_file, "w")
        f.write("Welcome to %s!\nHit <TAB> for boot options\n\n" % productName)
        f.close()
        os.chmod(message_file, 0600)

    def write_config_header(self, config):
        header = ("# silo.conf generated by anaconda\n\n"
                  "#boot=%(stage1dev)s\n"
                  "message=%(message)s\n"
                  "timeout=%(timeout)d\n"
                  "partition=%(boot_part_num)d\n"
                  "default=%(default)s\n"
                  % {"stage1dev": self.stage1_device.path,
                     "message": self.message_file, "timeout": self.timeout,
                     "boot_part_num": self.stage1_device.partedPartition.number,
                     "default": self.image_label(self.default)})
        config.write(header)
        self.write_config_password(config)

    def write_config_post(self, install_root=""):
        etc_silo = os.path.normpath(install_root + "/etc/" + self._config_file)
        if not os.access(etc_silo, os.R_OK):
            try:
                os.symlink("../boot/%s" % self._config_file, etc_silo)
            except OSError as e:
                log.warning("failed to create /etc/silo.conf symlink: %s" % e)

    def write_config(self, install_root=""):
        self.write_message_file(install_root=install_root)
        super(SILO, self).write_config(install_root=install_root)

    #
    # installation
    #

    def install(self, install_root=""):
        backup = "%s/backup.b" % self.config_dir
        args = ["-f", "-C", self.config_file, "-S", backup]
        variant = iutil.getSparcMachine()
        if variant in ("sun4u", "sun4v"):
            args.append("-u")
        else:
            args.append("-U")

        rc = iutil.execWithRedirect("silo", args,
                                    stdout="/dev/tty5", stderr="/dev/tty5",
                                    root=install_root)

        if rc:
            raise BootLoaderError("bootloader install failed")


""" anaconda-specific functions """

# this doesn't need to exist anymore, but the messageWindow probably needs to
# go somewhere
def bootloaderSetupChoices(anaconda):
    if anaconda.dir == DISPATCH_BACK:
        status = pyanaconda.view.Status()
        rc = status.need_answer_sync(_("Warning"),
                _("Filesystems have already been activated.  You "
                  "cannot go back past this point.\n\nWould you like to "
                  "continue with the installation?"),
                type="custom", custom_icon=["error","error"],
                custom_buttons=[_("_Exit installer"), _("_Continue")])

        if rc == 0:
            sys.exit(0)
        return DISPATCH_FORWARD


def writeSysconfigKernel(anaconda, default_kernel):
    f = open(anaconda.rootPath + "/etc/sysconfig/kernel", "w+")
    f.write("# UPDATEDEFAULT specifies if new-kernel-pkg should make\n"
            "# new kernels the default\n")
    # only update the default if we're setting the default to linux (#156678)
    if anaconda.bootloader.default.device == anaconda.storage.rootDevice:
        f.write("UPDATEDEFAULT=yes\n")
    else:
        f.write("UPDATEDEFAULT=no\n")
    f.write("\n")
    f.write("# DEFAULTKERNEL specifies the default kernel package type\n")
    f.write("DEFAULTKERNEL=%s\n" % default_kernel)
    f.close()


def writeBootloader(anaconda):
    """ Write bootloader configuration to disk.

        When we get here, the bootloader will already have a default linux
        image. We only have to add images for the non-default kernels and
        adjust the default to reflect whatever the default variant is.
    """

    # TODO: Verify the bootloader configuration has all it needs.
    #
    #       - zipl doesn't need to have a stage1 device set.
    #       - Isn't it possible for stage1 to be unset on iSeries if not using
    #         yaboot? If so, presumably they told us not to install any
    #         bootloader.
    stage1_device = anaconda.bootloader.stage1_device
    log.info("bootloader stage1 target device is %s" % stage1_device.name)
    stage2_device = anaconda.bootloader.stage2_device
    log.info("bootloader stage2 target device is %s" % stage2_device.name)

    status = pyanaconda.view.Status()
    status.i_am_busy(_("Bootloader"),
                     _("Installing bootloader."))

    # get a list of installed kernel packages
    kernel_versions = anaconda.backend.kernelVersionList(anaconda.rootPath)
    if not kernel_versions:
        log.warning("no kernel was installed -- bootloader config unchanged")
        status.need_answer_sync(_("Warning"),
                        _("No kernel packages were installed on the system. "
                          "Bootloader configuration will not be changed."))
        return

    # The first one is the default kernel. Update the bootloader's default
    # entry to reflect the details of the default kernel.
    (version, arch, nick) = kernel_versions.pop(0)
    default_image = anaconda.bootloader.default
    if not default_image:
        log.error("unable to find default image, bailing")
        status.no_longer_busy()
        return

    default_image.version = version

    # all the linux images' labels are based on the default image's
    base_label = default_image.label
    base_short = default_image.short_label

    # get the name of the default kernel package for use in
    # /etc/sysconfig/kernel
    default_kernel = "kernel"
    if nick != "base":
        default_kernel += "-%s" % nick

    # now add an image for each of the other kernels
    used = ["base"]
    for (version, arch, nick) in kernel_versions:
        if nick in used:
            nick += "-%s" % version

        used.append(nick)
        label = "%s-%s" % (base_label, nick)
        short = "%s-%s" % (base_short, nick)
        image = LinuxBootLoaderImage(device=anaconda.storage.rootDevice,
                                     version=version,
                                     label=label, short=short)
        anaconda.bootloader.add_image(image)

    # write out /etc/sysconfig/kernel
    writeSysconfigKernel(anaconda, default_kernel)

    # set up dracut/fips boot args
    anaconda.bootloader.set_boot_args(keyboard=anaconda.keyboard,
                                      language=anaconda.instLanguage,
                                      network=anaconda.network)

    try:
        anaconda.bootloader.write(install_root=anaconda.rootPath)
    except BootLoaderError as e:
        status.need_answer_sync(_("Warning"),
                            _("There was an error installing the bootloader.  "
                              "The system may not be bootable."))
    finally:
        status.no_longer_busy()
