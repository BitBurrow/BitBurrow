# BitBurrow: Manage base router

Connect a new base router to the BitBurrow hub.

## Notes

* These steps should be done at your "VPN home".
* Be aware that, by following these steps, you are giving this BitBurrow hub full control of the new base router.
* Below, "your device" refers to the phone, tablet, or laptop you are currently using to connect
to this website.

## Plugging in

1. Plug your new base router into wall power.
1. Plug an Ethernet cable between the "WAN" port on the new base router and one of the "LAN" ports on the existing router.

## Connecting WiFi

Connect your device to the new base router via WiFi. By default, it will have a WiFi name like `GL-SFT1200-xxx` where `SFT1200` is the router model and `xxx` is a 3-digit number. The default WiFi password is **`goodlife`**.

## Opening router web interface

On your device, browse to your base router's address, which is [http://192.168.8.1](http://192.168.8.1) by default.

## Signing in to router

If your base router is new, follow the directions to choose a language and create a new password. If you are asked to sign in but don't know the password, you will need to reset the router. For detailed instructions on how to factory-reset your router, see [How to repair network or factory reset?](https://docs.gl-inet.com/router/en/4/faq/repair_network_or_reset_firmware/) and [How to Reset GL.iNet Router to Factory Default 2022](https://www.youtube.com/watch?v=ON6PtGH_HJw).

## Navigating to LuCI

1. On the main menu for the base router, warnings such as "The interface is connected, but the internet can't be accessed." or "LAN subnet is in conflict with the WAN subnet, ..." can be safely ignored at this point.
1. In the menu on the left, choose SYSTEM → Advanced Settings and run LuCI via something like "Go To LuCI" or "192.168.8.1/cgi-bin/luci" or "Install Now" → "Go To LuCI".

## Signing in to LuCI

Log in with **`root`** and the password you chose above.

## Navigating to Local Startup

1. Click System → Startup ([direct link](http://192.168.8.1/cgi-bin/luci/admin/system/startup)).
1. If there is a tab near the top of the page labeled "Local Startup", click that. Otherwise, scroll down to the "Local Startup" section at the bottom of the page.

## Copying startup code

Copy the text from the block of code below (there's a button for this in the top-right corner).

{{ code_block(id='code_for_local_startup', text='', language='sh') }}

## Pasting startup code

Paste this text at the *beginning* of the existing text in the "Local Startup" text box and click Submit.

## Rebooting the router

1. Click System → Reboot → Perform reboot.
1. Wait a couple of minutes for the reboot. Your device may automatically reconnect to your existing router. If it does, reconnect to the new base router.
