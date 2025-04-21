#!/bin/bash
#
#    To set up a virtual OpenWrt system for testing:
#        lxc launch ubuntu: openwrt-host
#        lxc file push base/emulator/install.sh openwrt-host/home/ubuntu/emulator_install.sh
#        # configure passwordless ssh to openwrt-host
#        ssh openwrt-host bash emulator_install.sh
#
set -e

sudo apt update
sudo apt install -y qemu-system-mips wget bridge-utils expect
# AR750 architecture is mips_24kc (from `cat /proc/cpuinfo` and `opkg print-architecture`)
# docs: https://openwrt.org/docs/guide-user/virtualization/qemu#openwrt_in_qemu_mips
# below, 'be' is big-endian
# note--this works but changes are lost at reboot: qemu-system-mips -kernel *-vmlinux-initramfs.elf ...
DLPREFIX="https://downloads.openwrt.org/releases/24.10.0/targets/malta/be/openwrt-24.10.0-malta-be"
wget $DLPREFIX-vmlinux.elf --output-document=vmlinux.elf
wget $DLPREFIX-rootfs-squashfs.img.gz --output-document=rootfs-squashfs.img.gz
gunzip rootfs-squashfs.img.gz
# note--re disk image size, see AR750 specs: https://openwrt.org/toh/gl.inet/gl-ar750
qemu-img resize rootfs-squashfs.img -f raw +512M


cat << 'EOF_85581118558111' >"/home/ubuntu/launch_qemu.sh"
#!/bin/bash
#
/usr/bin/qemu-system-mips \
    -kernel vmlinux.elf \
    -drive file=rootfs-squashfs.img,format=raw \
    -append 'root=/dev/sda rootwait' \
    -nographic -m 256 \
    -serial mon:stdio \
    -netdev tap,id=hn0,ifname=tap0,script=no,downscript=no \
    -device pcnet,netdev=hn0,mac=random-mac-here
EOF_85581118558111
chmod 755 "/home/ubuntu/launch_qemu.sh"
# assign MAC to avoid OpenWrt errors: received packet on eth0 with own address as source address
# MAC must begin '52:54:00' and be otherwise unique
export RANDOM_MAC=$(printf '52:54:00:%02x:%02x:%02x\n' $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)))
perl -p -i -e "s|random-mac-here|$RANDOM_MAC|;" /home/ubuntu/launch_qemu.sh


cat << 'EOF_28577392857739' >"/home/ubuntu/first_boot.sh"
#!/usr/bin/env -S expect -f
#
set timeout 120
set send_slow {1 .1}
log_user 1
set ak_path [file normalize "~/.ssh/authorized_keys"]
set ak_fp [open $ak_path r]
set ak_data [read $ak_fp]
close $ak_fp
set ak_escaped [string map {"\\" "\\\\" "\"" "\\\""} $ak_data]
spawn -noecho stdbuf -o0 ./launch_qemu.sh
# invisible way to detect prompt; more reliable than 'expect "$"'
set prompt_suffix "\u200B\u200C\u200B"
expect "Please press Enter to activate this console"
set timeout 60
send -- "\n"
sleep 3
send -- "\n"
expect ":~# "
send -- "export PS1=\"\${PS1}$prompt_suffix\"\r"
# flush output to align commands with their prompts
interact -timeout 2 return
# use lappend instead of list form so Expect vars can be expanded; use "..." instead of {...} to evaluate $[\ etc.
set cmds {}
lappend cmds {sleep 7}
lappend cmds {echo -e 'Password1\nPassword1' |passwd root}
lappend cmds {uci set network.lan.proto='dhcp'}
lappend cmds {uci set network.lan.ifname='eth0'}
lappend cmds {uci commit network}
lappend cmds {/etc/init.d/network restart}
# unnecessary: lappend cmds {opkg update}
lappend cmds {opkg install dropbear}
lappend cmds {/etc/init.d/dropbear enable}
lappend cmds {/etc/init.d/dropbear start}
lappend cmds "echo -n \"$ak_escaped\" >/etc/dropbear/authorized_keys"
lappend cmds {chmod 600 /etc/dropbear/authorized_keys}
lappend cmds {/etc/init.d/dropbear restart}
# unnecessary: lappend cmds {uci add firewall rule}
# unnecessary: lappend cmds {uci set firewall.@rule[-1].name='allow-ssh'}
# unnecessary: lappend cmds {uci set firewall.@rule[-1].src='wan'}
# unnecessary: lappend cmds {uci set firewall.@rule[-1].dest_port='22'}
# unnecessary: lappend cmds {uci set firewall.@rule[-1].proto='tcp'}
# unnecessary: lappend cmds {uci set firewall.@rule[-1].target='ACCEPT'}
# unnecessary: lappend cmds {uci commit firewall}
# unnecessary: lappend cmds {/etc/init.d/firewall reload}
lappend cmds {printf '\n\n=== add to your ~/.ssh/config ===\nHost openwrt\nHostname '; ip route show default |grep ^default |grep -Eo ' src [0-9\.]+' |grep -Eo '[0-9\.]+'; printf 'User root\n\n\n'}
lappend cmds {sleep 7}
lappend cmds {halt}
foreach cmd $cmds {
    send -s -- "$cmd\r"
    flush stdout
    expect "$prompt_suffix"
}
EOF_28577392857739
chmod 755 "/home/ubuntu/first_boot.sh"


cat << 'EOF_94704499470449' |sudo tee "/etc/systemd/system/qemu.service" >/dev/null
[Unit]
Description=QEMU Emulator
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/home/ubuntu/launch_qemu.sh
Restart=on-failure
RestartSec=3
User=ubuntu
WorkingDirectory=/home/ubuntu

[Install]
WantedBy=multi-user.target
EOF_94704499470449
sudo chmod 644 "/etc/systemd/system/qemu.service"


cat << 'EOF_59204915920491' |sudo tee "/etc/systemd/network/01-br0.netdev" >/dev/null
[NetDev]
Name=br0
Kind=bridge
EOF_59204915920491
sudo chmod 644 "/etc/systemd/network/01-br0.netdev"


cat << 'EOF_53973095397309' |sudo tee "/etc/systemd/network/02-tap0.netdev" >/dev/null
[NetDev]
Name=tap0
Kind=tap

[Tap]
User=ubuntu
Group=ubuntu
EOF_53973095397309
sudo chmod 644 "/etc/systemd/network/02-tap0.netdev"


cat << 'EOF_75979117597911' |sudo tee "/etc/systemd/network/03-eth0-slave.network" >/dev/null
[Match]
Name=eth0

[Network]
Bridge=br0
DHCP=no
EOF_75979117597911
sudo chmod 644 "/etc/systemd/network/03-eth0-slave.network"


cat << 'EOF_32620043262004' |sudo tee "/etc/systemd/network/04-tap0-slave.network" >/dev/null
[Match]
Name=tap0

[Network]
Bridge=br0
EOF_32620043262004
sudo chmod 644 "/etc/systemd/network/04-tap0-slave.network"


cat << 'EOF_43311924331192' |sudo tee "/etc/systemd/network/00-br0.link" >/dev/null
[Match]
OriginalName=br0

[Link]
MACAddress=put-eth0-mac-here
EOF_43311924331192
sudo chmod 644 "/etc/systemd/network/00-br0.link"
export ETH0_MAC=$(ip link show eth0 |awk '/ether/ {print $2}')
sudo perl -p -i -e "s|put-eth0-mac-here|$ETH0_MAC|;" /etc/systemd/network/00-br0.link


cat << 'EOF_55344875534487' |sudo tee "/etc/systemd/network/99-br0.network" >/dev/null
[Match]
Name=br0

[Network]
DHCP=yes
KeepConfiguration=static
EOF_55344875534487
sudo chmod 644 "/etc/systemd/network/99-br0.network"


# for network-dependant services, watch br0 rather than eth0 to avoid 2-minute sshd delay
sudo mkdir -p /etc/systemd/system/systemd-networkd-wait-online.service.d/
cat << 'EOF_77856027785602' |sudo tee "/etc/systemd/system/systemd-networkd-wait-online.service.d/override.conf" >/dev/null
[Service]
ExecStart=
ExecStart=/usr/lib/systemd/systemd-networkd-wait-online  --any -o routable -i br0
EOF_77856027785602
sudo chmod 644 "/etc/systemd/system/systemd-networkd-wait-online.service.d/override.conf"
# alternative to above: EDITOR=vim systemctl edit systemd-networkd-wait-online.service


# alternative to "/etc/systemd/network/" files:
#    ip link add name br0 type bridge
#    ip link set dev br0 up
#    ip link set dev eth0 master br0
#    ip tuntap add tap0 mode tap user ubuntu
#    ip link set dev tap0 master br0
#    ip link set dev tap0 up


sudo systemctl enable systemd-networkd
sudo systemctl restart systemd-networkd
/home/ubuntu/first_boot.sh
sudo systemctl enable qemu.service
sudo reboot

