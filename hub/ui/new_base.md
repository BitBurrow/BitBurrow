# BitBurrow: New base router

{{ headline(text='**New base router**', align='center') }}

**Notes:**

* These steps should be done at your "VPN home".
* Be aware that, by following these steps, you are giving this BitBurrow hub full control of the new base router.
* Below, "your device" refers to the phone, tablet, or laptop you are currently using to connect
to this website.

**Steps:**

1. Plug your new base router into wall power.
1. Plug an Ethernet cable between the "WAN" port on the new base router and one of the "LAN" ports on the existing router.
1. Connect your device to the new base router via WiFi. By default, it will have a WiFi name like `GL-SFT1200-xxx` where `SFT1200` is the router model and `xxx` is a 3-digit number. The default WiFi password is **`goodlife`**.
1. On your device, browse to [http://192.168.8.1](http://192.168.8.1), where "192.168.8" is the
new subnet of your device.
1. New GL-iNet routers do not have a password set. If you are asked to sign in but don't know the password, you will need to reset the router. For detailed instructions on how to factory-reset your router, see [How to repair network or factory reset?](https://docs.gl-inet.com/router/en/4/faq/repair_network_or_reset_firmware/) and [How to Reset GL.iNet Router to Factory Default 2022](https://www.youtube.com/watch?v=ON6PtGH_HJw).
1. If asked, select a language and a new password.
1. Warning such as "The interface is connected, but the internet can't be accessed." or "LAN subnet is in conflict with the WAN subnet, ..." can be safely ignored at this point.
1. In the menu on the left, choose SYSTEM → Advanced Settings and click the LuCI option at the bottom (e.g. "Go To LuCI" or "192.168.8.1/cgi-bin/luci").
1. Log in with `root` and the password you chose above.
1. Click System → Startup ([direct link](http://192.168.8.1/cgi-bin/luci/admin/system/startup)).
1. If there is a tab near the top of the page labeled "Local Startup", click that. Otherwise, scroll down to the "Local Startup" section at the bottom of the page.
1. Copy the text from the block of code below (there's a button for this in the top-right corner).
1. Paste this text at the *beginning* of the existing text in the "Local Startup" text box and click Submit.
1. Click System → Reboot → Perform reboot.
1. Wait a couple of minutes for the reboot. Your device may automatically reconnect to your existing router. If it does, reconnect to the new base router.

{{ code_block(id='code_for_local_startup', text='', language='sh') }}
