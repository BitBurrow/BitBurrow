# BitBurrow: Welcome

{{ headline(text='**Welcome to BitBurrow**', align='center') }}

BitBurrow lets you carry your home internet with you, giving non-technical
users seamless and secure access from anywhere, without common VPN headaches.

{{ image(source='bitburrow.png', align='center', width='50%') }}

*(This project is currently in development. Most of the features described below are
not yet implemented.)*

If you travel, live part-time in another place, or work remotely, you have
probably run into annoying VPN issues: websites that block you, banking apps
that require additional verification, streaming services that show the wrong
country, or complications with setting up VPN software.

BitBurrow is a way to route your internet traffic via your home when you travel.
Instead of paying for (and trusting) a VPN provider, you install a small BitBurrow
router at your home and take another one with you when you travel. Devices connected
to the travel router behave as if they were sitting at your home. Once set up, you
just use the internet normally. No extra apps are required, and any device that can connect
to WiFi can take advantage of BitBurrow's private and secure connection to your
base router.

{{ comment(text='''ChatGPT prompt for the following image:

Please create this image.

The image is rendered in a 3D minimalist, stylized aesthetic. The people, objects, and devices have smooth, matte surfaces and soft, rounded edges. The image communicates abstract concepts of how two network devices interact. It conveys a blend of efficiency, organization, and aesthetic appreciation. The plain background keeps the focus on the devices and network topology.

On the right side of the image is a 45-year-old woman wearing grey business-casual attire, sitting at a simple light grey desk and working on a laptop. Also on the desk, left of the laptop, is a very small, green WiFi router.

On the left side of the image is an identical-looking very small, green router sitting on an identical-looking light grey desk, but there is no laptop or person.

For both green routers, use dark spring green, defined as html #177245.

The two desks are a couple of meters apart, while a dark, dotted, curved line connects the router on the left-hand desk with the router on the right-hand desk.''') }}

{{ image(source='2routers2tables.png', align='center', width='100%') }}

Tap a section header below to learn more.

## What problem does BitBurrow solve?

When you use a commercial VPN, your traffic appears to come from a shared data center.
Many websites restrict those connections because cyber criminals and bots use them too.
This sometimes reveals itself in strange ways. For example, you may be required to
complete extra steps to log in to a banking website. Other sites may appear to be
inexplicably down or display messages like "Service Unavailable" or "The specified URL
cannot be found".

BitBurrow avoids these issues because your internet traffic comes from a normal
residential or office connection that belongs to you or someone you trust. From the
website's point of view, you are simply at home.

## In simple terms, how does BitBurrow work?

One router stays at your home. This becomes your "base router". Another router travels
with you or lives at your second home. Your phones and laptops connect to the travel
router via normal WiFi. The travel router connects out to the base router, creates
a private encrypted tunnel, and routes all of your internet traffic through that tunnel.

(For those who are curious, here are the very technical details. The base router runs
OpenWrt. It establishes a WireGuard connection to the
[BitBurrow hub](https://bitburrow.com/hub/) for management and Dynamic DNS. If the base
router is behind a firewall, the hub coordinates opening an in-bound UDP port via UPnP or,
if needed, configuring the firewall router. Finally, the travel router uses DNS
(managed by the BitBurrow hub) to locate the base router, establishes a WireGuard
connection, and forwards all traffic through it.)

## What are BitBurrow's limitations?

* The base router must be located in a safe, reliable site with a good internet connection.
  Your BitBurrow connection depends on your home internet staying online. Similarly,
  download speeds over BitBurrow are limited by the *upload* speed of the base router's
  internet connection and, in some cases, by the CPU speed of the two routers.
* If you need access to VPN servers in many locations, BitBurrow is probably impractical.
* If you only need a VPN occasionally, the one-time cost of two routers may be
  cost-prohibitive.

## What do I need to get started?

1. A coupon code for a BitBurrow hub. If you do not have access to one, you can
   [set up your own hub](https://bitburrow.com/hub/) (this requires some Linux
   background) or ask your company or organization about doing this.
1. Two Flint routers (**GL.iNet GL-AX1800**), available from
   [GL.iNet](https://store.gl-inet.com/collections/smart-home-gateway-mesh-router/products/flint-gl-ax1800-dual-band-gigabit-wifi-6-openwrt-adguard-home)
   and [Amazon.com](https://amazon.com/dp/B09HBW45ZJ)
   and [Walmart](https://www.walmart.com/ip/-/187628398)
   and [other locations](https://www.gl-inet.com/where-to-buy/#europe).
1. Permission to set up a new base router at the chosen location.
1. If you plan to use the existing router in front of the base router, you
   will likely need the login password for that router.
1. An Android phone or tablet which can be used at the base router location.

# ..

Please enter your coupon code below (if it is not already filled in).

{{ input(id='coupon_code', login_key=True, label='Coupon code',
placeholder='XXXX-XXXXX-XXXX-XXXXX', font_size='18px', icon='confirmation_number') }}

{{ button(id='continue', text="Continue") }}
