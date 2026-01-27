# BitBurrow: Welcome

{{ headline(text='**Welcome to BitBurrow**', align='center') }}

{{ image(source='bitburrow.png', align='center', width='50%') }}

BitBurrow is a set of tools to help you set up and use a VPN "base" anywhere--at your parents' house, an office, or a
friend's apartment. And you don't have to be good with computers. A BitBurrow base will allow you to securely use the
internet from anywhere in the world as if you were at your "VPN home".

## What you will need

1. A coupon code for a BitBurrow hub. If you do not have access to one, you can [set up your own hub](https://bitburrow.com/hub/) (this requires some Linux background) or ask your company or organization about doing this.
1. Two Flint routers (**GL.iNet GL-AX1800**), available from [GL.iNet](https://store.gl-inet.com/collections/smart-home-gateway-mesh-router/products/flint-gl-ax1800-dual-band-gigabit-wifi-6-openwrt-adguard-home) and [Amazon.com](https://amazon.com/dp/B09HBW45ZJ) and [WalMart](https://www.walmart.com/ip/-/187628398) and [other locations](https://www.gl-inet.com/where-to-buy/#europe).
1. Permission to set up a new router at your "VPN home" location.
1. If you plan to continue to keep using the existing router at your "VPN home", you will need the login password for this
router.
1. An Android phone or tablet which can be used at your "VPN home".

## Deciding if this is the right tool for you

* Why would you **want** to do this

    * **Restrictions**. When you are on a commercial VPN, websites often add additional security checks or outright block service. When this happens, rarely is it clear *why*. A VPN via a residential or business location is not subject to these restrictions.
    * **Cost**. Typically the cost of the VPN server hardware over its lifetime is much less than monthly VPN service fees. The existing internet connection is probably sufficient. The software is free.
    * **Trust**. Many commercial VPN providers log VPN connections and online activities.
    * **Access**. Banks, Netflix, and other sites will allow you to use their services as if you were at your "VPN home", even if you are physically in another country.
    * **Firewalls**. WiFi at airports and coffee shops often blocks known VPN servers, but a non-commercial VPN is less likely to be blocked.

* Why would you **not** want to do this

    * **Speed**. Internet speed over the VPN will be limited by the devices at both ends, and also by the upload speed of the "VPN home" internet connection, since this will be used for downloading via the VPN.
    * **Reliability**. If the power is out or a cable gets unplugged at the VPN server location, the VPN will be unavailable. If you depend on a VPN, have multiple options.
    * **Global servers**. Commercial VPN providers usually have VPN servers in dozens of countries. With BitBurrow, you are limited to the servers you set up or friends invite you to use.

## Process overview

1. Scan your QR-code for the BitBurrow coupon or enter the information manually.
1. You will be given a login key and be asked to write it down in a safe place.
1. Plug in the new router and connect the Android device to its WiFi.
1. If everything works as planned, BitBurrow will configure your router as a VPN server, prompting you if necessary. The process normally takes a few minutes.
1. You can use the same BitBurrow app to add, edit, and delete VPN client devices (phones, laptops, other routers, etc.) to use the internet through your "VPN home" location.

# ..

Please enter your coupon code below (if it is not already filled in).

{{ input(id='coupon_code', login_key=True, label='Coupon code', placeholder='XXXX-XXXXX-XXXX-XXXXX', font_size='18px', icon='confirmation_number') }}

{{ button(id='continue', text="Continue") }}


