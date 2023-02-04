import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'dart:ui'; // for SemanticsFlag
import 'package:bitburrow/main.dart' as app;

//
// RUN THESE TESTS VIA:
//   flutter test integration_test
//

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('GUI integrations tests', (WidgetTester t) async {
    app.main();
    await waitForConnectingDialog(t, welcomeScreenOkay: true);
    await ellipsisMenuItem(t, "Forget login key");
    expect(find.text("Sign in"), findsOneWidget);
    await ellipsisMenuItem(t, "Enter a coupon code");
    expect(find.text("Welcome to BitBurrow"), findsOneWidget);
    await ellipsisMenuItem(t, "Sign in");
    // these 2 should have been cleared by forgetLoginKey()
    expect(checkboxValue(t, r'Store my login k'), false);
    expect(accountFieldValue(t, r"Login key"), "");
    await t.tap(find.bySemanticsLabel(RegExp(r"Store my login k")));
    await t.pumpAndSettle();
    expect(checkboxValue(t, r'Store my login k'), true);
    await signIn(t);
    await ellipsisMenuItem(t, "Enter a coupon code");
    expect(find.text("Welcome to BitBurrow"), findsOneWidget);
    await ellipsisMenuItem(t, "Servers");
    await waitForConnectingDialog(t);
    await ellipsisMenuItem(t, "Enter a coupon code");
    expect(find.text("Welcome to BitBurrow"), findsOneWidget);
    await ellipsisMenuItem(t, "Sign in");
    await ellipsisMenuItem(t, "Forget login key");
    expect(find.text("Sign in"), findsOneWidget);
    // these 2 should have been cleared by forgetLoginKey()
    expect(checkboxValue(t, r'Store my login k'), false);
    expect(accountFieldValue(t, r"Login key"), "");
    await ellipsisMenuItem(t, "Enter a coupon code");
    expect(find.text("Welcome to BitBurrow"), findsOneWidget);
    await ellipsisMenuItem(t, "Sign in");
    expect(checkboxValue(t, r'Store my login k'), false);
    expect(accountFieldValue(t, r"Login key"), "");
    await ellipsisMenuItem(t, "Sign in");
    await t.tap(find.bySemanticsLabel(RegExp(r"Store my login k")));
    await signIn(t);
    await ellipsisMenuItem(t, "Sign in");
    expect(checkboxValue(t, r'Store my login k'), true);
    expect(accountFieldValue(t, r"Login key"), "•••••••••••••••••••••");
    await ellipsisMenuItem(t, "Enter a coupon code");
    expect(find.text("Welcome to BitBurrow"), findsOneWidget);
    final couponField = find.bySemanticsLabel(RegExp(r"Coupon"));
    await t.enterText(couponField, "RX72PXQFTFFC69D3C6");
    await t.pumpAndSettle();
    expect(accountFieldValue(t, r"Coupon"), "RX72-PXQFT-FFC6-9D3C6");
    await t.tap(find.bySemanticsLabel("I trust this hub.*"));
    await t.tap(find.bySemanticsLabel("SUBMIT"));
    await waitForConnectingDialog(t);
    await t.tap(find.bySemanticsLabel("I HAVE WRITTEN IT DOWN"));
    await t.pumpAndSettle();
    await t.tap(find.bySemanticsLabel("SIGN IN"));
    await t.pumpAndSettle();
    expect(find.text("Please fix the errors in red before submitting."),
        findsOneWidget);
    await ellipsisMenuItem(t, "Enter a coupon code");
    expect(find.text("Welcome to BitBurrow"), findsOneWidget);
    expect(accountFieldValue(t, r"Coupon"), "RX72-PXQFT-FFC6-9D3C6");
    // ignore: avoid_print
    print("YTIMTREGA tests passed");
  });
}

Future<void> signIn(WidgetTester t) async {
  await ellipsisMenuItem(t, "Sign in");
  expect(find.text("Sign in"), findsOneWidget);
  final loginKeyField = find.bySemanticsLabel(RegExp(r"Login key"));
  await t.enterText(loginKeyField, "h67qj8mf8vm6mfzztd");
  await t.pumpAndSettle();
  expect(accountFieldValue(t, r"Login key"), "•••••••••••••••••••••");
  await t.tap(find.bySemanticsLabel("SIGN IN"));
  await waitForConnectingDialog(t);
}

Future<void> ellipsisMenuItem(WidgetTester t, String text) async {
  await t.pumpAndSettle();
  await t.tap(find.byTooltip("Menu"));
  await t.pumpAndSettle();
  await t.tap(find.widgetWithText(PopupMenuItem<String>, text));
  await t.pumpAndSettle();
}

bool checkboxValue(WidgetTester t, String regex) {
  var d = t
      .getSemantics(find.bySemanticsLabel(RegExp(regex)))
      .getSemanticsData()
      .hasFlag(SemanticsFlag.isChecked);
  return d;
}

String accountFieldValue(WidgetTester t, String regex) {
  final loginKeyField = find.bySemanticsLabel(RegExp(regex));
  return t.getSemantics(loginKeyField).value;
}

Future<void> waitForConnectingDialog(WidgetTester t,
    {welcomeScreenOkay = false}) async {
  var expectedMatchCount = 1;
  for (var i = 0; i < 150; i++) {
    await t.pumpAndSettle();
    final dialogBox = find.text("Connecting to hub ...");
    if (t.widgetList(dialogBox).length == expectedMatchCount) {
      if (expectedMatchCount == 0) return;
      expectedMatchCount = 0;
    }
    if (welcomeScreenOkay) {
      // at start-up, if login key was not saved last run, dialog is never shown
      final welcomeScreenText = find.text("Welcome to BitBurrow");
      if (t.widgetList(welcomeScreenText).length == 1) return;
    }
    await Future.delayed(const Duration(milliseconds: 250));
  }
  expect(expectedMatchCount, 0, reason: "Dialog box never found");
  expect(expectedMatchCount, -1, reason: "Dialog box did not complete");
}
