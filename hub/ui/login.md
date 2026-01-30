# BitBurrow: Log in

{{ headline(text='**Log in**', align='center') }}

{{ image(source='padlock-24051.svg', align='center', width='50%') }}

Please enter your login key below. If you don't have one, you can use a coupon code
to [create a new login key](/welcome).

{{ input(id='login_key', login_key=True, password=True, label='Login key',
placeholder='XXXX-XXXXX-XXXX-XXXXX', font_size='18px', icon='key') }}

{{ checkbox(id='keep_me_logged_in', label="Keep me logged in for 30 days") }}

{{ button(id='continue', text="Continue") }}
