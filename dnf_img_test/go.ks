#version=DEVEL
sshpw --username=root --plaintext randOmStrinGhERE
# Firewall configuration
firewall --enabled --service=mdns
# Use network installation
url --url="http://dl.fedoraproject.org/pub/fedora/linux/releases/19/Fedora/x86_64/os/"
repo --name=hawkey --baseurl=file:///home/akozumpl/hawkey/tests/repos/yum/

# X Window System configuration information
xconfig  --startxonboot
# Root password
rootpw --plaintext removethispw
# Network information
network  --bootproto=dhcp --device=eth0 --onboot=on --activate
# System authorization information
auth --useshadow --enablemd5
# System keyboard
keyboard us
# System language
lang en_US.UTF-8
# SELinux configuration
selinux --disabled
# Installation logging level
logging --level=info
# Shutdown after installation
shutdown
# System services
services --disabled="network,sshd" --enabled="NetworkManager"
# System timezone
timezone  US/Eastern
# System bootloader configuration
bootloader --location=mbr
# Clear the Master Boot Record
zerombr
# Partition clearing information
clearpart --all
# Disk partitioning information
part biosboot --size=1
part / --fstype="ext4" --size=4000
part swap --size=1000

%packages
#@core
tour
%end
