# BitBurrow: Confirmation

{{ headline(text='**Here is your new login key:**', align='center') }}

{{ image(source='padlock-24051.svg', align='center', width='50%') }}

{{ input(id='login_key', readonly=True, align='center', font_size='18px', copy_button=True) }}

Copy this login key to a safe place, such as secure notes or a password manager. If your login
key is lost, you will loose the ability to make changes to your BitBurrow devices.
{{ checkbox(id='have_written_down', label="I have written this down") }}{{ checkbox(id='keep_me_logged_in', label="Keep me logged in for 1 month") }}

{{ button(id='continue', text="Continue") }}
