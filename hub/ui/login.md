# Login


<input placeholder="login key" style='width: 100%;' id='keyinput'>

<button id="login">Login</button>
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


Don't have a login key? <a href='welcome'>Register</a>.

<script src='code_field.js'></script>
<script>
    const input = document.getElementById('keyinput')
    setInputField(input);

    const loginButton = document.getElementById('login');

    if (varifyCode(input.value)) {
        loginButton.disabled = false;
    }
    else {
        loginButton.disabled = true;
    }

    input.addEventListener('input', () => {
        document.getElementById('statusMessage').textContent = '';
        if (varifyCode(input.value)) {
            loginButton.disabled = false;
        }
        else {
            loginButton.disabled = true;
        }
    });

    loginButton.addEventListener('click', async () => {
        const statusMessage = document.getElementById('statusMessage');
        const loading = document.getElementById('loading');

        statusMessage.textContent = '';
        loading.style.display = 'inline';
        try {
            const res = await fetch('/v1/login/' + input.value.replace(/-/g, '') + '/');
            loading.style.display = 'none';
            if (await res.status == 200) {
                location.href = '/bases';
            }
            else {
                statusMessage.textContent = "Invalid login key.";
            }
        }
        catch (error) {
            loading.style.display = 'none';
            statusMessage.textContent = "Network error. Please check your internet connection and try again.";
        }
    });

</script>