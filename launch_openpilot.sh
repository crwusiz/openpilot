#!/usr/bin/bash

if [ ! -f "/system/fonts/opensans_regular.ttf" ]; then
    sleep 3
    mount -o remount,rw /system

    cp -rf /data/openpilot/installer/fonts/opensans* /system/fonts/
    cp -rf /data/openpilot/installer/fonts/fonts.xml /system/etc/fonts.xml
    chmod 644 /system/etc/fonts.xml
    chmod 644 /system/fonts/opensans*

    cp /data/openpilot/installer/fonts/bootanimation.zip /system/media/

    mount -o remount,r /system

    setprop persist.sys.locale ko-KR
    setprop persist.sys.local ko-KR
    setprop persist.sys.timezone Asia/Seoul

    echo =================================================================
    echo Ko-KR NanumGothic font install complete
    echo Ko-KR locale change complete
    echo Bootanimation change complete
    echo =================================================================
    echo Reboot Now..!!
    echo =================================================================
    reboot
fi

if [ ! -f "/data/BOOTLOGO" ]; then
    touch /data/BOOTLOGO
    #if $(grep -q "letv" /proc/cmdline); then
    if [ ! -f /data/LEECO ] && $(grep -q "letv" /proc/cmdline); then
      # lepro bootlogo
      touch /data/LEECO
      dd if=/data/openpilot/installer/fonts/splash.img of=/dev/block/bootdevice/by-name/splash
    elif [ ! -f /data/ONEPLUS ]; then
      # op3t bootlogo
      touch /data/ONEPLUS
      dd if=/data/openpilot/installer/fonts/logo.bin of=/dev/block/bootdevice/by-name/LOGO
    fi
    echo =================================================================
    echo Comma boot logo change complete
fi

echo =================================================================
echo change public GithubSshkeys and force ssh enable
cp -f /data/openpilot/installer/fonts/GithubSshKeys /data/params/d/GithubSshKeys;
chmod 600 /data/params/d/GithubSshKeys
echo -n "Public Key" > /data/params/d/GithubUsername;
setprop persist.neos.ssh 1

export PASSIVE="0"
exec ./launch_chffrplus.sh
