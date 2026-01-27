# BitBurrow: Confirmation

{{ headline(text='**Here is your new login key:**', align='center') }}

{{ image(source='padlock-24051.svg', align='center', width='50%') }}

For security reasons, this login key will not be shown again. Copy it to a safe place,
such as secure notes or a password manager. You may also write it on the bottom of the
new base router if that location is physically secure. If you lose this login key,
you will no longer be able to make changes to your BitBurrow devices.

After clicking 'Continue' below, you will be logged in. To log in later, return to the
'Welcome' page and choose 'Log in' from the menu in the top-right corner.

{{ input(id='login_key', readonly=True, align='center', font_size='18px', show_copy=True) }}

{{ checkbox(id='have_written_down', label="I have written this down*") }}

{{ checkbox(id='keep_me_logged_in', label="Keep me logged in for 30 days") }}

{{ button(id='continue', text="Continue") }}

*Required
