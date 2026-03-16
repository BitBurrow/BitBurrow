chatgpt_prompt = [[
Write a Lua script for OpenWrt that will run as a service or daemon.

Coding notes: Use snake_case for variables. Do not include unnecessary blank lines in the code. Include comments only when the code doesn't speak for itself. Use single quotes for strings everywhere except where the contents is free-form English (e.g. error messages). Avoid short functions, i.e. just in-line one-line functions or short functions that are only called from one place. Files in `/tmp` below, except for the log, are only examples. Use unpredictable temp filenames, or avoid temp files when possible.

The script does the following:

On startup:
* if we are not running as root, exit with an error message
* if this script is already running in another process (e.g. an atomic `mkdir` lock directory), exit with an error message
* read `/etc/bitburrow/api_url` and save it in the variable api_url; exit with an error message if the file is unreadable
* read `/etc/bitburrow/subd` and save it in the variable subd; exit with an error message if the file is unreadable
* read `/etc/bitburrow/token` and save it in the variable token; if the file is unreadable, set token to '' (an empty string)
* create `/tmp/bitburrow.log` (if it exists, overwrite) with permissions 0600 and throughout this script, write warnings and errors to this file (when appropriate) instead of stdout or stderr
* in `/etc/bitburrow/`, if `client_rsapss.pem` doesn't exist:
    * delete `client_rsapss_pub.pem` (failure is okay)
    * run `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out client_rsapss.pem`
    * set 0600 permissions on the private key created above
    * run `openssl pkey -in client_rsapss.pem -pubout -out client_rsapss_pub.pem`
    * include this code comment: note: OpenSSL 1.1.1 found on test routers can't sign with Ed25519 keys
* in `/etc/bitburrow/`, if `wgbb1_private.key` doesn't exist:
    * delete `wgbb1_public.key` (failure is okay)
    * run `wg genkey |tee wgbb1_private.key |wg pubkey >wgbb1_public.key`
    * set 0600 permissions on the private key created above
* if either the RSA or WG private key above has a timestamp newer than `/etc/bitburrow/pubkeys_uploaded`:
    * set the variable retry_wait to 7
    * set the variable retries_left to 2
    * create a JSON-RPC 2.0 request, "method" is "bootstrap1", and parameters:
        * "subd": variable subd
        * "token": variable token
        * "auth_pubkey": contents of the RSS public key file
        * "wg_pubkey": contents of the WG public key file
    * send this via something like `curl -X POST ${api_url}devices/rpc -H "Content-Type: application/json" --data @/tmp/rpc_request.json`
    * expect a JSON-RPC success response; anything else is considered to be a failure
    * if the request fails:
        * log the error
        * sleep for retry_wait seconds with jitter, i.e. plus or minus a randomized 50%, i.e. if retry_wait is 100, sleep for a random interger number of seconds between 50 and 150 seconds
        * decrement retries_left
        * if retries_left is 0 or less:
            * double retry_wait, but set to 3600 if it is more than that
            * set retries_left to 2
        * resend
    * after receiving a successful response, update the timestamp on `/etc/bitburrow/pubkeys_uploaded` with the current time
* repeat forever:
    * create a JSON-RPC 2.0 request, method "ping" with these values:
        * "subd": variable subd
        * "time": date/time in UTC, format '%Y-%m-%dT%H:%M:%SZ'
        * "uptime": the full output of `uptime`
        * "nonce": output of `openssl rand -hex 16`
    * use recommended values for the RFC 9421 covered components; the server code will be changed to match this code
    * compute the sha256sum of the above JSON and use for the Content-Digest header, according to RFC 9530
    * compute the signature according to RFC 9421, something like `openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:-1 -sign client_rsapss.pem -binary -out /tmp/api_data.sig /tmp/api_data`
    * base64-encode the signature and include in the headers
    * include a comment: verify: openssl dgst -sha256 -sigopt rsa_padding_mode:pss -sigopt rsa_pss_saltlen:-1 -verify /tmp/client_rsapss_pub.pem -signature /tmp/api_data.sig /tmp/api_data
    * send the RPC via curl
    * expect a JSON-RPC success response with a string "pingback" value; anything else is considered to be a failure
    * if the RPC call fails:
        * do the incremental backoff described above, i.e. wait for a period
    * if the RPC call is successful:
        * put "pingback" value in the log file
        * wait 60 seconds with 20% jitter
]]
print(chatgpt_prompt)
