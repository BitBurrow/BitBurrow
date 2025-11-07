prompt = """
I would like to use the Python library NiceGUI to build a web app. The full documentation for NiceGUI in JSON format is here: https://nicegui.io/static/sitewide_index.json

Write a NiceGUI app that starts with a welcome page and allows the user to either create a new account or log in.

On the "Create account" page, they can enter a username (using characters from the re `[a-zA-Z_.-]` with a length between 4 and 12, inclusive). If the username contains any invalid characters (checked with live validation), a "Use only letters, underscores, dashes, and periods." message should appear under the input box and the "Next" button should be disapbled until the offending characters are removed from the input box.

After clicking "Next", the app should check for duplicate usernames using  case-insensitive comparison. If any are found, return the user to the "Create account" page with an appropriate explanation in red at the bottom.

If there are no duplicate usernames, the user is assigned random 5-character password made up of numbers and lower-case letters. (I know this is really low entropy. This is for testing only.) The password is displayed once (ever) on the "Confirm password" page, along with a checkbox which says, "I have stored this password in a safe place." The "Next" button is disabled until the checkbox is checked. When "Next" is pressed, the user data is stored and the user is logged in. Then the user is taken to the home page (described below). If the user navigates away from the "Confirm password" page, all of the data is discarded.

The user data is stored in a SQLite database on the FastAPI server. Use a SQLModel class for the user account data, as well as a LoginSession class for keeping track of devices where the user is logged in. The password should only be stored using Argon2, like this:

```
hasher = argon2.PasswordHasher()
account.key_hash = hasher.hash(key)
```

If the user chooses to log in from the welcome page, they are presented with username and password input boxes, and if they enter the proper credentials, they are logged in and allowed to continue to the home page.

The home page simply displays "You are on the home page", shows a list of devices (brief description based on the User Agent string, IP address (from the `X-Real-IP` header), time/date of last activity, and if it is still valid), were the user can select devices with a checkbox and invalidate them, i.e. log them out of the device. There should also be a "Select all" button which will check all of the devices. The current device should also be noted on the list as the current device.

Be sure to only allow authenticated users access to the home page.

Enforce a rate limit of 10 requests per minute per IP address.

The app should listen on port 8080 and be configured to run behind a TLS-terminating reverse proxy. The proxy traffic will come from a different IP address. It is not necessary to try to block non-proxy traffic.

Do not use `ui.card()` for any of the pages.

What questions do you have? What is unclear about this prompt?
"""
