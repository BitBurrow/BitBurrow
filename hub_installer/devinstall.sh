#!/bin/bash
set -e # exit script if anything fails

## how to use a BitBurrow hub for development testing

#
# 1. clone the repo:
#        mkdir "where_you_keep_code/bitburrow" && cd "$_"
#        git clone https://github.com/BitBurrow/BitBurrow.git .
# 2. make changes
# 3. stop BitBurrow hub service on your_hub (as user ubuntu or root):
#        sudo systemctl stop bitburrow
# 4. copy your modified files to ~/bitburrow/ on your_hub via something similar to:
#        rsync -av --delete --files-from <(git ls-files; git ls-files --others --exclude-standard) \
#            ./ bitburrow@your_hub:bitburrow/
# 5. run this script:
#        bash bitburrow/hub_installer/devinstall.sh
# 6. run BitBurrow hub in debug mode:
#        bitburrow/.venv/bin/bbhub --daemon -vv
#

## install BitBurrow hub

cd ~/bitburrow/
poetry install
