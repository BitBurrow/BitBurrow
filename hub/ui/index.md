<title>Welcome to BitBurrow</title>

# Welcome to BitBurrow

BitBurrow is a set of tools to help you set up and use a VPN "base" anywhere—at your parents— house, an office, or a
friend—s apartment. And you don—t have to be good with computers. A BitBurrow base will allow you to securely use the
internet from anywhere in the world as if you were at your "VPN home". For more information, see the <a
    href='https://bitburrow.com/' target='_blank' rel='noopener'>BitBurrow overview</a>.

## What you will need:
1. A coupon code for a BitBurrow hub.<sup>&dagger;</sup>
2. Two Flint routers (<strong>GL.iNet GL-AX1800</strong>), available from GL.iNet, Amazon.com, Walmart, and other
locations.
3. Permission to set up a new router at your "VPN home" location.
4. If you plan to continue to use the existing router at your "VPN home", you will need the login password for this
router.
5. An Android phone or tablet which can be used at your "VPN home".

<input placeholder="Enter your coupon code" style='width: 100%;' id='codeinput'>

<sup>&dagger;</sup> If you do not have access to a coupon code, you can set up your own hub (this requires some computer
experience) or ask your company or organization about doing this.

<button id='continue'>Continue</button>
<div id='statusMessage' style='color: red; margin-top: 1em;'></div>
<div id='loading' style='display:none;'>
    <div
        style='border:4px solid #f3f3f3;border-top:4px solid #3498db;border-radius:50%;width:24px;height:24px;animation:spin 1s linear infinite;'>
    </div>
    <style>
        @keyframes spin {
            0% {
                transform: rotate(0deg)
            }

            100% {
                transform: rotate(360deg)
            }
        }
    </style>
</div>

Already registered? <a href='login'>Login here</a>.


<script src='code_field.js'></script>

<script>
    //auto fill input from URL
    const params = new URLSearchParams(window.location.search);
    var q = params.get('coupon');
    if (q != null) {
        q = filterChars(q);
        var out = '';
        for (var i = 0; i < q.length; i++) {
            out += q[i];
            if (dashesAfter.includes(i)) {
                out += '-';
            }
        }
        document.getElementById('codeinput').value = out;
    }

    const continueButton = document.getElementById('continue');

    const input = document.getElementById('codeinput');
    setInputField(input);


    input.addEventListener('input', () => {
        document.getElementById('statusMessage').textContent = '';
        if (varifyCode(input.value)) {
            continueButton.disabled = false;
        }
        else {
            continueButton.disabled = true;
        }
    });


    if (varifyCode(input.value)) {
        continueButton.disabled = false;
    }
    else {
        continueButton.disabled = true;
    }
    continueButton.addEventListener('click', async () => {

        const statusMessage = document.getElementById('statusMessage');
        const loading = document.getElementById('loading');

        statusMessage.textContent = '';
        loading.style.display = 'inline';
        try {
            const res = await fetch('/v1/coupons/' + input.value.replace(/-/g, '') + '/managers');
            loading.style.display = 'none';
            if (await res.status == 201) {
                const data = await res.json()
                location.href = '/show_login_key?key=' + data.login_key;
            }
            else {
                statusMessage.textContent = "Invalid coupon code.";
            }
        }
        catch (error) {
            loading.style.display = 'none';
            statusMessage.textContent = "Network error. Please check your internet connection and try again.";
        }
    });
</script>