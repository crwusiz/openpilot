#!/usr/bin/bash

if [ ! -f "/system/fonts/opensans_regular.ttf" ]; then
    sleep 3
    mount -o remount,rw /system

    cp -rf /data/openpilot/installer/fonts/opensans* /system/fonts/
    cp -rf /data/openpilot/installer/fonts/NotoColorEmoji.ttf /system/fonts/
    cp -rf /data/openpilot/installer/fonts/fonts.xml /system/etc/fonts.xml
    chmod 644 /system/etc/fonts.xml
    chmod 644 /system/fonts/opensans*
    chmod 644 /system/fonts/NotoColorEmoji.ttf

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
    /usr/bin/touch /data/BOOTLOGO
    dd if=/data/openpilot/installer/fonts/splash.img of=/dev/block/bootdevice/by-name/splash
    echo =================================================================
    echo Comma boot logo change complete
fi

echo =================================================================
echo GithubSshkeys change public key
cp -f /data/openpilot/installer/fonts/GithubSshKeys /data/params/d/GithubSshKeys;
chmod 600 /data/params/d/GithubSshKeys

export PASSIVE="0"
exec ./launch_chffrplus.sh

