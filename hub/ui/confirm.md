# BitBurrow: Confirmation

{{ headline(text='**Here is your new login key:**', align='center') }}

{{ image(source='padlock-24051.svg', align='center', width='50%') }}

{{ comment(text='''Possible alternate to "This is the only ...": For security reasons
this login key will not be displayed again.''') }}

This is the only time the login key below will be shown. Copy it to a safe place, such
as secure notes or a password manager. If your login key is lost, you will loose the
ability to make changes to your BitBurrow devices.

After clicking 'Continue' below, you will be logged in. To log in in the future, go
to the 'Welcome' page that you just came from and click 'Log in' from the menu in the
top-right corner.

{{ input(id='login_key', readonly=True, align='center', font_size='18px', show_copy=True) }}

{{ checkbox(id='have_written_down', label="I have written this down*") }}

{{ checkbox(id='keep_me_logged_in', label="Keep me logged in for 30 days") }}

{{ button(id='continue', text="Continue") }}

*Required
