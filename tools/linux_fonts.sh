#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# Make Helvetica resolve to Liberation Sans on a Linux machine, as CI and the Pages
# build need.
#
# Sheet text is set in Helvetica and laid out with Helvetica's widths
# (plannotation.export.sheet.text_width). Linux has no Helvetica, and fontconfig
# otherwise substitutes whatever it finds first, so text laid out for Helvetica could
# overflow its box. Liberation Sans (SIL OFL 1.1) has Arial's metrics, which are
# Helvetica's. The font is installed from the distribution, never committed.
#
# Debian or Ubuntu, with sudo. The rule goes in a file of its own under the user's
# fontconfig conf.d, which fontconfig's stock 50-user.conf reads, so the user's own
# fonts.conf is left alone; it applies to every program the user runs, so delete that
# file to undo it. Fails unless fc-match then answers Liberation Sans.
set -eu

if ! fc-list 2>/dev/null | grep -q 'Liberation Sans'; then
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends fontconfig fonts-liberation
fi

rules="${XDG_CONFIG_HOME:-$HOME/.config}/fontconfig/conf.d"
mkdir -p "$rules"
cat > "$rules/60-plannotation-helvetica.conf" <<'CONF'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <!-- Helvetica is set as Liberation Sans, which has its metrics. -->
  <match target="pattern">
    <test qual="any" name="family"><string>Helvetica</string></test>
    <edit name="family" mode="assign" binding="strong"><string>Liberation Sans</string></edit>
  </match>
</fontconfig>
CONF
fc-cache -f

family=$(fc-match -f '%{family}' Helvetica)
if [ "$family" != "Liberation Sans" ]; then
  echo "error: Helvetica resolves to '$family', not Liberation Sans" >&2
  exit 1
fi
echo "Helvetica resolves to $family"
