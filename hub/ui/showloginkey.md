<title>Write down login key</title>

# account created
write down key

<input type="text" readonly style='width: 100%;text-align: center;' id='loginkeyinput'>

...

more text

<input type="checkbox" class='required check'> check this box

<input type="checkbox" class='required check'> also check this box

<button disabled id='continuebutton'>Continue</button>

<script>
    const params = new URLSearchParams(window.location.search);
    var loginKey = params.get('key');
    document.getElementById('loginkeyinput').value = loginKey;
    document.cookie = 'loginkey=' + loginKey + '; max-age=31536000; path =/; SameSite=Strict; Secure';


    const continueButton = document.getElementById('continuebutton')

    const chechboxes = document.getElementsByClassName('required check');
    Array.from(chechboxes).forEach(chechbox => {
        chechbox.checked = false;
        chechbox.addEventListener('change', () => {
            var allChecked = true;
            Array.from(chechboxes).forEach(c => {
                if (!c.checked) {
                    allChecked = false;
                }
            })
            if (allChecked) {
                continueButton.disabled = false;
            }
            else {
                continueButton.disabled = true;
            }
        })
    });

    continueButton.disabled = true;
    continueButton.addEventListener("click", () => {
        location.href = '/configure_router'
    })
</script>