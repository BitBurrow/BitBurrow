// ignore_for_file: avoid_print

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart' as storage;
import 'dart:io' as io;
import 'welcome_screen.dart';
import 'new_login_key_screen.dart';
import 'sign_in_screen.dart';
import 'servers_screen.dart';
import 'new_server_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await onAppStart();
  runApp(App());
}

class LoginState {
  String hub = ""; // saved in secure storage
  String coupon = "";
  String newLoginKey = "";
  String loginKey = ""; // saved in secure storage
  bool loginKeyVerified = false; // saved in secure storage
  bool saveLoginKey = false; // user choice to keep login key in secure storage
  List<int> servers = [];

  bool isSignedIn() => loginKeyVerified;
  bool isNotSignedIn() => !loginKeyVerified;
  String get pureCoupon => coupon.replaceAll('-', ''); // for API calls
  String get pureLoginKey => loginKey.replaceAll('-', ''); // for API calls

  // convert to display version used in this app, e.g. 'X88L-7V2BC-MM3P-RKVF2'
  String dressLoginKey(String pureLoginKey) {
    return '${pureLoginKey.substring(0, 4)}-${pureLoginKey.substring(4, 9)}-'
        '${pureLoginKey.substring(9, 13)}-${pureLoginKey.substring(13)}';
  }
}

var loginState = LoginState();

Future<void> onAppStart() async {
  // fixme: user sees blank screen until this completes; implement a loading
  // ... screen if this method takes too long
  try {
    var keyStore = const storage.FlutterSecureStorage();
    loginState.hub = (await keyStore.read(key: 'hub')) ?? "";
    loginState.loginKey = (await keyStore.read(key: 'login_key')) ?? "";
    loginState.loginKeyVerified =
        (await keyStore.read(key: 'login_key_verified') == 'true');
    // login key is saved if and only if it's verified and user opts to store it
    loginState.saveLoginKey = loginState.loginKeyVerified;
  } catch (err) {
    if (io.Platform.environment['DBUS_SESSION_BUS_ADDRESS'] == 'disabled:') {
      // VSCode Linux issue - https://github.com/electron/electron/issues/31981
      // add this to .bashrc: unset DBUS_SESSION_BUS_ADDRESS
      print("B70101 D-Bus has been disabled: $err");
    } else {
      print("B52325 can't read from secure storage: $err");
    }
    return;
  }
}

Page<void> ourPageBuilder(
        BuildContext context, GoRouterState state, Widget child) =>
    CustomTransitionPage<void>(
      key: state.pageKey,
      child: child,
      transitionsBuilder: // change the default MaterialApp transition
          (context, animation, secondaryAnimation, child) =>
              FadeTransition(opacity: animation, child: child),
    );

class App extends StatelessWidget {
  App({Key? key}) : super(key: key);

  static const String title = 'BitBurrow';

  static const ourPrimaryColor = 0xff5b5b5b;
  final int r = (ourPrimaryColor & 0x00ff0000) ~/ 0x10000;
  final int g = (ourPrimaryColor & 0x0000ff00) ~/ 0x100;
  final int b = (ourPrimaryColor & 0x000000ff);
  late final Map<int, Color> ourColorCodes = {
    50: Color.fromRGBO(r, g, b, .1),
    100: Color.fromRGBO(r, g, b, .2),
    200: Color.fromRGBO(r, g, b, .3),
    300: Color.fromRGBO(r, g, b, .4),
    400: Color.fromRGBO(r, g, b, .5),
    500: Color.fromRGBO(r, g, b, .6),
    600: Color.fromRGBO(r, g, b, .7),
    700: Color.fromRGBO(r, g, b, .8),
    800: Color.fromRGBO(r, g, b, .9),
    900: Color.fromRGBO(r, g, b, 1),
  };

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: title,
      theme: ThemeData(
        primarySwatch:
            MaterialColor(ourPrimaryColor, ourColorCodes), // checkboxes
        colorScheme: ColorScheme.fromSwatch().copyWith(
          primary: const Color(ourPrimaryColor),
          secondary: const Color(0xffd3a492),
          background: const Color(0xffff0000), // seems unused
        ),
      ),
      routeInformationProvider: _router.routeInformationProvider,
      routeInformationParser: _router.routeInformationParser,
      routerDelegate: _router.routerDelegate,
    );
  }

  final GoRouter _router = GoRouter(
    routes: <GoRoute>[
      GoRoute(
        path: '/',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const WelcomeScreen()),
      ),
      GoRoute(
        path: '/new-login-key',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const NewLoginKeyScreen()),
      ),
      GoRoute(
        path: '/sign-in',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const SignInScreen()),
      ),
      GoRoute(
        path: '/servers',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const ServersScreen()),
      ),
      GoRoute(
        path: '/new-server',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const NewServerScreen()),
      ),
    ],
    redirect: (state) {
      if (state.subloc == '/' && loginState.loginKeyVerified) {
        // if we have valid login key, skip welcome screen and proceed with automatic sign-in
        return '/sign-in';
      }
      return null;
    },

    urlPathStrategy:
        UrlPathStrategy.path, // turn off the extra `#/` in the URLs
  );
}

MarkdownBody textMd(BuildContext context, md) {
  return MarkdownBody(
    selectable:
        false, // DO NOT USE; see https://stackoverflow.com/questions/73491527
    styleSheet: MarkdownStyleSheet.fromTheme(Theme.of(context)).copyWith(
        textScaleFactor: 1.14, // match default Text() widget size
        a: const TextStyle(
          color: Color.fromARGB(255, 40, 128, 22),
          decoration: TextDecoration.underline,
        ),
        p: const TextStyle(height: 1.35)), // less cramped vertically
    onTapLink: (text, url, title) {
      if (url == null) return;
      if (url[0] == '/') {
        context.push(url); // within our app
      } else {
        launchUrl(Uri.parse(url));
      }
    },
    data: md,
  );
}

Widget ourScreenLayout(BuildContext context, Widget body,
        {Widget? floatingActionButton}) =>
    Scaffold(
      appBar: AppBar(
          // title: const Text(App.title),
          toolbarHeight: 40.0,
          actions: <Widget>[
            if (GoRouter.of(context).location != '/sign-in')
              IconButton(
                  icon: const Icon(Icons.login),
                  tooltip: "Sign in",
                  onPressed: () {
                    context.push('/sign-in');
                  }),
            IconButton(
                icon: const Icon(Icons.more_vert),
                tooltip: "More",
                onPressed: () {}),
          ]),
      body: body,
      floatingActionButton: floatingActionButton,
    );
