How to install on Oneplus 3t
------
1. clone openpilot to /data/ and make sure it's named openpilot:

```
cd /data/ && rm -fr openpilot; git clone https://github.com/crwusiz/openpilot.git openpilot
```

2. run command:

```
cd /data/openpilot/installer && ./op3t_neos_update.sh
```

3. Let it download and complete it update, after a couple of reboot, your screen will then stay in fastboot mode.


4. In fastboot mode, select use volume button to select to `Recovery mode` then press power button.


5. In Recovery mode, tap `apply update` -> `Choose from emulated` -> `0/` -> `update.zip` -> `Reboot system now`


6. You should be able to boot into openpilot, `if touch screen is not working`, try to `reboot` again.
