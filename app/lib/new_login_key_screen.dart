import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'package:logging/logging.dart';
import 'dart:math';
import 'main.dart';

final _log = Logger('new_login_key_screen');
var loginState = LoginState.instance;

class NewLoginKeyScreen extends StatelessWidget {
  const NewLoginKeyScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 24);
    return ourScreenLayout(
      context,
      SingleChildScrollView(
        restorationId: 'new_login_key_screen_scroll_view',
        padding: const EdgeInsets.symmetric(horizontal: 34),
        child: Column(
          children: [
            // center elements vertically if less than screen height
            Container(
                // fixme: replace 550 with the actual height of widgets below
                height: max((MediaQuery.of(context).size.height - 550) / 2, 0)),
            sizedBoxSpace,
            const FractionallySizedBox(
              widthFactor: 0.8,
              child: Text(
                "Here is your new login key:",
                textAlign: TextAlign.center,
                textScaleFactor: 1.8,
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            sizedBoxSpace,
            FractionallySizedBox(
              widthFactor: 0.6,
              child: SvgPicture.asset("images/padlock-24051.svg"),
            ),
            sizedBoxSpace,
            FractionallySizedBox(
              widthFactor: 0.8,
              child: Text(
                loginState.newLoginKey,
                textAlign: TextAlign.center,
                textScaleFactor: 1.8,
                style: const TextStyle(fontWeight: FontWeight.normal),
              ),
            ),
            sizedBoxSpace,
            textMd(
                context,
                "Before continuing, write this down in a safe place. You "
                "will need it in the future to make changes to your router "
                "and VPN settings."),
            sizedBoxSpace,
            sizedBoxSpace,
            Center(
              child: ElevatedButton(
                onPressed: () {
                  _log.fine(
                      "ElevatedButton 'I HAVE WRITTEN IT DOWN' onPressed()");
                  context.push('/sign-in');
                },
                child: const Text("I HAVE WRITTEN IT DOWN"),
              ),
            ),
            sizedBoxSpace,
          ],
        ),
      ),
    );
  }
}
