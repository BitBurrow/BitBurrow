<title>Welcome to BitBurrow</title>

# Welcome to BitBurrow

BitBurrow is a set of tools to help you set up and use a VPN "base" anywhere—at your parents— house, an office, or a friend—s apartment. And you don—t have to be good with computers. A BitBurrow base will allow you to securely use the internet from anywhere in the world as if you were at your "VPN home". For more information, see the <a href="https://bitburrow.com/" target="_blank" rel="noopener">BitBurrow overview</a>.

## What you will need:
1. A coupon code for a BitBurrow hub.<sup>&dagger;</sup>
2. Two Flint routers (<strong>GL.iNet GL-AX1800</strong>), available from GL.iNet, Amazon.com, Walmart, and other locations.
3. Permission to set up a new router at your "VPN home" location.
4. If you plan to continue to use the existing router at your "VPN home", you will need the login password for this router.
5. An Android phone or tablet which can be used at your "VPN home".

<input placeholder="Enter your coupon code" style="width: 100%;" id="codeinput">

<sup>&dagger;</sup> If you do not have access to a coupon code, you can set up your own hub (this requires some computer experience) or ask your company or organization about doing this.

<button id="continue">Continue</button>






<script>
    const maxLength = 21;
    const maxLengthPure = 18;
    const allowedChars = "23456789BCDFGHJKLMNPQRSTVWXZ";
    const dashesAt = [4, 10, 15];
    const dashesAfter = [4 - 1, 10 - 2, 15 - 3];

    var varifyCode = (str) => {
        for (var i = 0; i < str.length; i++) {
            if (dashesAt.includes(i)) {
                if (str[i] != '-') {
                    return false;

                }
            } else {
                if (!allowedChars.includes(str[i])) {
                    return false;
                }
            }
        }
        if (str.length != maxLength) {
            return false;
        }
        return true;
    };

    var filterChars = (chars) => {
        chars = chars.toUpperCase();
        var out = "";
        for (var i = 0; i < chars.length; i++) {
            if (allowedChars.includes(chars[i])) {
                out += chars[i];
            }
        }
        return out;
    };


    //auto fill input from URL
    const params = new URLSearchParams(window.location.search);
    var q = params.get('q');
    if (q != null) {
        q = filterChars(q);
        var out = "";
        for (var i = 0; i < q.length; i++) {
            out += q[i];
            if (dashesAfter.includes(i)) {
                out += '-';
            }
        }
        document.getElementById("codeinput").value = out;
    }

    const continueButton = document.getElementById("continue");

    //handle dashes
    const input = document.getElementById('codeinput');

    var lastInputState = input.value;
    input.addEventListener('input', () => {
        var typingPlace = input.selectionStart;
        if (input.value[typingPlace] != '-' && lastInputState[typingPlace] == '-' && input.value.length < lastInputState.length) {
            input.value = input.value.slice(0, typingPlace - 1) + input.value.slice(typingPlace);
            typingPlace--;
        }
        var rawIn = filterChars(input.value);
        var out = '';
        for (var i = 0; i < rawIn.length; i++) {
            out += rawIn[i];
            if (dashesAfter.includes(i)) {
                out += "-";
            }
        }
        input.value = out;
        if (out.length > maxLength) {
            input.value = lastInputState;
            input.value = input.value.substring(0, Math.min(input.value.length, maxLength));
        }
        lastInputState = input.value;
        if (out[typingPlace] == "-") { typingPlace++; }
        input.selectionStart = typingPlace;
        input.selectionEnd = typingPlace;

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
    continueButton.addEventListener("click", () => {
        location.href = '/couponcode?q=' + input.value.replace(/-/g, '');
    });


</script>
