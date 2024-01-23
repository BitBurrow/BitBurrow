#!/bin/bash
set -e # exit script if anything fails

## how to use a BitBurrow hub for development testing

#
# 1. clone the repo:
#        mkdir "where_you_keep_code/bitburrow" && cd "$_"
#        git clone https://github.com/BitBurrow/BitBurrow.git .
# 2. make changes
# 3. stop BitBurrow hub service (as user ubuntu or root):
#        sudo systemctl stop bitburrow
# 4. copy your modified files to ~/dev/hub via something similar to:
#        rsync -av --files-from <(git ls-files; git ls-files --others --exclude-standard) \
#            ./ bitburrow@your_hub:dev/hub/
# 5. run this script:
#        bash dev/hub/hub_installer/devinstall.sh
# 6. run BitBurrow hub in debug mode:
#        .local/bin/bbhub --daemon -vv
#

## download BitBurrow hub dependencies

cd ~/
TO_INSTALL="dev/hub/"
# if last updated 47+ hours ago or never, or dependencies have changed
if ! [ -f dev/dependencies/pyproject.toml.md5sum ] \
        || find dev/dependencies/pyproject.toml.md5sum -mmin +2820 |grep -q last_updated \
        || ! md5sum --check --status dev/dependencies/pyproject.toml.md5sum; then
    echo ======= downloading BitBurrow hub dependencies =======
    python3 -m pip download $TO_INSTALL poetry-core --dest dev/dependencies/
    md5sum dev/hub/pyproject.toml >dev/dependencies/pyproject.toml.md5sum
fi

## install BitBurrow hub from ~/dev/hub/ and local download cache

echo ======= pip-installing BitBurrow hub =======
python3 -m pip install -qq $TO_INSTALL --no-index --find-links dev/dependencies/

echo ======= finished installing =======
