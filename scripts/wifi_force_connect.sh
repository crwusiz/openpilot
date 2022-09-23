#!/usr/bin/bash

echo "  Wifi force connect"
nmcli -w 5 dev wifi con 'Android' password '12345678'