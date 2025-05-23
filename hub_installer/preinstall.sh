#!/bin/bash
set -e # exit script if anything fails

## install dependencies
sudo apt update
sudo apt install -y wget ansible

## verify $SUDO_USER
test "x$SUDO_USER" != "x" || (echo "Run this script as a normal user using 'sudo'."; false)
test "x$SUDO_USER" != "xroot" || (echo "Run this script as a normal user using 'sudo'."; false)
SUDO_USER_HOME=$(eval echo "~$SUDO_USER")

## enable `ssh localhost` for $SUDO_USER
cat <<"_EOF6471_" |sudo --user $SUDO_USER bash
mkdir -p ~/.ssh/
if test ! -f ~/.ssh/id_ed25519; then
    ssh-keygen -q -f ~/.ssh/id_ed25519 -N '' -t ed25519
fi
if ! (grep $(cat ~/.ssh/id_ed25519.pub |awk '{print $2}') ~/.ssh/authorized_keys); then
    cat ~/.ssh/id_ed25519.pub >>~/.ssh/authorized_keys
    echo "* $(cat /etc/ssh/ssh_host_ecdsa_key.pub)" >>~/.ssh/known_hosts
fi
chmod go-w ~ ~/.ssh
chmod ugo-x,go-w ~/.ssh/authorized_keys
mkdir -p ~/hub/
_EOF6471_

## create Ansible script that does most of the installing
cat <<"_EOF3703_" >$SUDO_USER_HOME/hub/install.yaml
---
- hosts: localhost
  become: true
  tasks:

  - name: Ensure remote_tmp directory exists with correct permissions
    file:
      path: /home/bitburrow/.ansible/tmp
      state: directory
      mode: '0755'
      owner: bitburrow
      group: bitburrow

  - name: Run apt upgrade
    apt:
      upgrade: "yes"  # quotes avoid https://github.com/ansible/ansible/issues/56788
      update_cache: true
      cache_valid_time: 432000  # ≥5 days

  - name: Install package dependencies
    # debugging: df --si /  # check in container *and* on host
    apt:
      name:
      - unattended-upgrades
      - python3-poetry  # to install BitBurrow
      - wireguard-tools
      - sqlite3
      - acl  # `setfacl` below and https://stackoverflow.com/a/56379678
      - bind9-dnsutils  # `dig` tool
      state: latest

  - name: Define poetry path
    set_fact:
      poetry: /usr/bin/poetry
    changed_when: false

  - name: Enable automatic security updates
    # requires package unattended-upgrades
    # see also https://help.ubuntu.com/community/AutomaticSecurityUpdates
    copy:
      content: |
        APT::Periodic::Update-Package-Lists "1";
        APT::Periodic::Unattended-Upgrade "1";
        APT::Periodic::Download-Upgradeable-Packages "1";
        APT::Periodic::AutocleanInterval "7";
      dest: /etc/apt/apt.conf.d/20auto-upgrades
      mode: '0644'

  - name: Add BitBurrow hub user
    user:
      name: bitburrow
      password: '!'  # disabled
      state: present
      create_home: true
      shell: /bin/bash

  - name: Allow sudo without password for specific commands
    copy:
      content: |
        bitburrow  ALL = NOPASSWD: /usr/bin/wg *
        bitburrow  ALL = NOPASSWD: /usr/bin/ip *
        bitburrow  ALL = NOPASSWD: /usr/bin/sysctl *
        bitburrow  ALL = NOPASSWD: /usr/bin/iptables *
        bitburrow  ALL = NOPASSWD: /usr/sbin/wg *
        bitburrow  ALL = NOPASSWD: /usr/sbin/ip *
        bitburrow  ALL = NOPASSWD: /usr/sbin/sysctl *
        bitburrow  ALL = NOPASSWD: /usr/sbin/iptables *
        bitburrow  ALL = NOPASSWD: /bin/wg *
        bitburrow  ALL = NOPASSWD: /bin/ip *
        bitburrow  ALL = NOPASSWD: /bin/sysctl *
        bitburrow  ALL = NOPASSWD: /bin/iptables *
        bitburrow  ALL = NOPASSWD: /sbin/wg *
        bitburrow  ALL = NOPASSWD: /sbin/ip *
        bitburrow  ALL = NOPASSWD: /sbin/sysctl *
        bitburrow  ALL = NOPASSWD: /sbin/iptables *
      dest: /etc/sudoers.d/bitburrow
      mode: '0640'

  - name: Install bbhub 🦶1--git clone
    git:
      repo: https://github.com/BitBurrow/BitBurrow.git
      dest: /home/bitburrow/bitburrow/
      version: main
      update: true
      depth: 1
    become_user: bitburrow
    register: git_result

  - name: Install bbhub 🦶2--configure Poetry so we can find .../bin/bbhub
    command:
      cmd: "{{ poetry }} config virtualenvs.in-project true"
    become_user: bitburrow
    changed_when: false
    when: git_result.changed

  - name: Install bbhub 🦶3--install
    shell: |-
      cd /home/bitburrow/bitburrow/
      {{ poetry }} install
    become_user: bitburrow
    when: git_result.changed

  - name: Define bbhub path
    set_fact:
      bbhub: /home/bitburrow/bitburrow/.venv/bin/bbhub
    changed_when: false

  - name: Set domain
    command:
      cmd: "{{ bbhub }} --set-domain {{ domain }}"
    become_user: bitburrow
    when: domain is defined

  - name: Define get_domain
    command:
      cmd: "{{ bbhub }} --get-domain"
    register: get_domain
    changed_when: false
    become_user: bitburrow

  - name: Set ip
    command:
      cmd: "{{ bbhub }} --set-ip {{ ip }}"
    become_user: bitburrow
    when: ip is defined

  - name: Define get_ip
    command:
      cmd: "{{ bbhub }} --get-ip"
    register: get_ip
    changed_when: false
    become_user: bitburrow

  - name: Define hostname
    command:
      cmd: hostname --short
    register: hostname
    changed_when: false
    become_user: bitburrow

  - name: Create port forward script
    copy:
      content: |
        #!/bin/bash
        ##
        VMNAME={{ hostname.stdout }}
        bbhub() { lxc exec $VMNAME -- sudo -u bitburrow {{ bbhub }} "$1"; }
        ##
        ## Configure port forwarding from host to container for BitBurrow hub
        ##
        APIPORT=8443  # hard-coded in app
        WGPORT=$(bbhub --get-wg-port)
        lxc config device add $VMNAME apiport proxy listen=tcp:0.0.0.0:$APIPORT connect=tcp:127.0.0.1:$APIPORT
        lxc config device add $VMNAME wgport proxy listen=udp:0.0.0.0:$WGPORT connect=udp:127.0.0.1:$WGPORT
        ##
        ## Allow logging of client IP addresses (otherwise all connections appear to be from 127.0.0.1)
        ##
        # from https://discuss.linuxcontainers.org/t/making-sure-that-ips-connected-to-the-containers-gameserver-proxy-shows-users-real-ip/8032/5
        VMIP=$(lxc list $VMNAME -c4 --format=csv |grep -o '^\S*')
        lxc stop $VMNAME
        lxc config device override $VMNAME eth0 ipv4.address=$VMIP
        lxc config device set $VMNAME apiport nat=true listen=tcp:{{ get_ip.stdout }}:$APIPORT connect=tcp:0.0.0.0:$APIPORT
        lxc start $VMNAME
        ##
        ## Configure port forwarding from host to container for BIND
        ##
        lxc config device add $VMNAME udpdns proxy listen=udp:{{ get_ip.stdout }}:53 connect=udp:127.0.0.1:53
        lxc config device add $VMNAME tcpdns proxy listen=tcp:{{ get_ip.stdout }}:53 connect=tcp:127.0.0.1:53
        ##
      dest: /home/bitburrow/set_port_forwarding.sh
      mode: '0755'
    become_user: bitburrow

  - name: Create systemd service file
    copy:
      content: |
        [Unit]
        Description=BitBurrow
        Documentation=https://bitburrow.com
        After=network.target network-online.target
        #
        [Service]
        Type=exec
        RestartSec=2s
        User=bitburrow
        Group=bitburrow
        WorkingDirectory=/home/bitburrow/bitburrow
        ExecStart={{ poetry }} run python3 {{ bbhub }} --daemon
        Restart=always
        PrivateTmp=true
        PrivateDevices=false
        NoNewPrivileges=false
        #
        [Install]
        WantedBy=multi-user.target
      dest: /etc/systemd/system/bitburrow.service
      mode: '0660'

  - name: Enable systemd service file
    # debugging: sudo systemctl status bitburrow
    # debugging: sudo journalctl -u bitburrow
    systemd:
      name: bitburrow
      enabled: yes
      state: started
      masked: false

  - name: Install BIND 🦶1--install
    apt:
      name:
      - bind9
      state: latest

  - name: Install BIND 🦶2--options file
    # changes from default BIND named.conf.options file:
    # * remove comments, blank lines
    # * disable recursive resolver--add line: recursion no;
    # * disable version, hostname, etc. (`dig @[server ip] hostname.bind TXT CHAOS`)
    copy:
      content: |
        options {
            directory "/var/cache/bind";
            dnssec-validation auto;
            listen-on-v6 { any; };
            recursion no;
            version none;
            hostname none;
            server-id none;
        };
      dest: /etc/bind/named.conf.options
    register: bind_options

  - name: Install BIND 🦶3--add zone config
    blockinfile:
      block: |
        zone "{{ get_domain.stdout }}" {
          type master;
          file "/var/cache/bind/db.{{ get_domain.stdout }}";
          allow-transfer { none; };
          update-policy local;
        };
      path: /etc/bind/named.conf.local
    register: bind_zone_config

  - name: Install BIND 🦶4--add zone file
    # re CAA record, see https://letsencrypt.org/docs/caa/
    # FIXME: support ACME-CAA - https://www.devever.net/~hl/acme-caa-live and https://news.ycombinator.com/item?id=34035148
    copy:
      content: |
        ;
        ; BIND data file for {{ get_domain.stdout }}
        ;
        $TTL  500
        @ IN  SOA  {{ get_domain.stdout }}. root.localhost. (
                    5    ; Serial
               604800    ; Refresh
                86400    ; Retry
              2419200    ; Expire
                  500 )  ; Negative Cache TTL
        ;
        @ IN  NS   {{ get_domain.stdout }}.
        @ IN  A    {{ get_ip.stdout }}
        @ IN  CAA  0 issue "letsencrypt.org"
      dest: /var/cache/bind/db.{{ get_domain.stdout }}
      force: false  # do not overwrite if file exists (BIND rewrites this file)
      mode: '0644'
      owner: bind
      group: bind
    register: bind_zone_file

  - name: Install BIND 🦶5--restart
    # debugging: sudo named-checkconf  # should display nothing
    # debugging: printf "zone vxm.example.org\nupdate delete testa.vxm.example.org.\nupdate add testa.vxm.example.org. 600 IN A 9.9.9.9\nsend\n" |sudo -u bind /usr/bin/nsupdate -l  # substitute your domain; see that no errors are displayed
    # debugging: dig @127.0.0.1 testa.vxm.example.org +short  # again, substitute your domain
    systemd:
      name: bind9
      enabled: yes
      state: restarted
    when: bind_options.changed or bind_zone_config.changed or bind_zone_file.changed
  
  - name: Test BIND and DNS config
    command:
      cmd: "{{ bbhub }} --test dig"
    become_user: bitburrow
    changed_when: false

  - name: Wildcard TLS cert 🦶1--install certbot
    # same as: sudo snap install --classic certbot
    snap:
      name: certbot
      classic: yes

  - name: Wildcard TLS cert 🦶2--hook file
    # debugging: sudo certbot renew --dry-run
    # debugging: sudo systemctl list-timers snap.certbot.renew.timer
    copy:
      content: |
        #!/bin/bash
        DNS_ZONE={{ get_domain.stdout }}
        HOST='_acme-challenge'
        sudo -u bind /usr/bin/nsupdate -l << EOM
        zone ${DNS_ZONE}
        update delete ${HOST}.${CERTBOT_DOMAIN} A
        update add ${HOST}.${CERTBOT_DOMAIN} 300 TXT "${CERTBOT_VALIDATION}"
        send
        EOM
        sleep 5
      dest: /opt/certbot_hook.sh
      mode: '0550'
      owner: bind
      group: bind

  - name: Wildcard TLS cert 🦶3--request cert
    shell:
      cmd: >
        certbot certonly -n --agree-tos
        --manual --manual-auth-hook=/opt/certbot_hook.sh
        --preferred-challenge=dns
        --register-unsafely-without-email
        -d '*.'{{ get_domain.stdout }} -d {{ get_domain.stdout }}
        --server https://acme-v02.api.letsencrypt.org/directory
        && touch /etc/letsencrypt/.registered
      creates: /etc/letsencrypt/.registered  # once it completes successfully, never run again

  - name: Wildcard TLS cert 🦶4--fix permissions so bbhub can read cert
    command:
      cmd: setfacl -Rm d:user:bitburrow:rx,user:bitburrow:rx /etc/letsencrypt/
    changed_when: false

_EOF3703_

## display a message
echo
echo "The script '$0' completed successfully."
echo
echo "You could use one of these (or something entirely different) as a prefix for your domain name:"
echo
echo -n "    "
for i in $(seq 1 24); do
    LC_ALL=C tr -dc a-z </dev/urandom |head -c 1
    LC_ALL=C tr -dc a-z0-9 </dev/urandom |head -c 2
    echo -n " "
done
echo
echo
echo "Your public IP address appears to be:"
echo
echo -n "    "
wget -q -O- api.bigdatacloud.net/data/client-ip |grep ipString |grep -Eo [0-9\.]+
echo
