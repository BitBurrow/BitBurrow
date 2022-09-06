import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:go_router/go_router.dart';
import 'package:flutter_svg/flutter_svg.dart';
import 'dart:math';

void main() {
  runApp(App());
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

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: title,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSwatch().copyWith(
          primary: const Color(0xFF343A40),
          secondary: const Color(0xFFFFC107),
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
        routes: <GoRoute>[
          // has back arrow to root page
          GoRoute(
            path: 'page2',
            pageBuilder: (context, state) =>
                ourPageBuilder(context, state, const Page2Screen()),
          ),
        ],
      ),
      GoRoute(
        path: '/page3',
        pageBuilder: (context, state) =>
            ourPageBuilder(context, state, const Page3Screen()),
      ),
    ],
    urlPathStrategy:
        UrlPathStrategy.path, // turn off the extra `#/` in the URLs
  );
}

var text1 =
    'Ut **bold** and *ital* and [pub.dev](https://pub.dev) necessitatibus '
    '[page 2 with back arrow](/page2) and [page 3](/page3) and [back to home page](/) '
    'dignissimos rerum et fuga sapiente et dicta internos non '
    'odio repudiandae? Ut repellat amet est ducimus doloremque est similique nobis '
    'qui explicabo molestiae. Qui sunt porro vel quas officia nam porro galisum! '
    '\n\n';
var text2 =
    'Lorem ipsum dolor sit amet. Non magni internos eum quis omnis et eveniet '
    'repellendus eos illo quas est voluptatibus minima ut explicabo enim. Ea nobis '
    'corporis sit voluptas nihil in labore minima quo similique velit ex distinctio '
    'laboriosam vel iste excepturi non sunt alias. Et eligendi odio est impedit '
    'voluptatem et animi necessitatibus ut quod dignissimos non aspernatur veniam '
    'aut illum minima. Qui iure facere id mollitia doloribus et eaque nemo vel enim '
    'molestiae et consequuntur quas et quae quia. '
    '\n\n'
    'Aut magnam incidunt et '
    'earum voluptate vel voluptates minus et illo et necessitatibus doloribus et '
    'temporibus accusamus. In vitae alias non corrupti ullam ad galisum velit. Ea '
    'laudantium minus id quam quae rem illum magnam id provident veniam id facilis '
    'sunt ad rerum officiis sed minima unde. '
    '\n\n';
var text3 =
    'Ut Quis sint ea sequi assumenda qui ullam fuga et debitis tenetur id dolores '
    'dolorum quo vitae dolores quo odit obcaecati! Id fugiat temporibus qui '
    'doloremque adipisci ut consectetur impedit hic possimus molestiae ea iste '
    'saepe. Ut eligendi error nam cumque magnam et dolorem omnis et enim ratione '
    'sed repellat fugiat eum perspiciatis facilis et voluptatem quod. Sed voluptas '
    'repudiandae et recusandae excepturi aut eligendi laborum et obcaecati dolorem '
    'et quidem nisi et voluptate quod vel numquam illo! '
    '\n\n';

void onMarkdownClick(BuildContext context, String url) {
  if (url[0] == '/') {
    // within our app
    context.go(url);
  } else {
    launchUrl(Uri.parse(url));
  }
}

MarkdownBody textMd(BuildContext context, md) {
  return MarkdownBody(
    selectable:
        false, // DO NOT USE; see https://stackoverflow.com/questions/73491527
    // fixme: read from a file: https://developer.school/tutorials/how-to-display-markdown-in-flutter
    styleSheet: MarkdownStyleSheet.fromTheme(ThemeData(
        textTheme: const TextTheme(
            bodyText2: TextStyle(
      fontSize: 16.0,
      color: Colors.black,
    )))),
    onTapLink: (text, url, title) {
      onMarkdownClick(context, url!);
    },
    data: md,
  );
}

Widget ourScreenLayout(BuildContext context, Widget child) => Scaffold(
      appBar: AppBar(
        // title: const Text(App.title),
        toolbarHeight: 40.0,
      ),
      body: SingleChildScrollView(
          child: Padding(
        padding: const EdgeInsets.all(18.0),
        child: child,
      )),
    );

class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        const WelcomeForm(),
      );
}

class WelcomeForm extends StatefulWidget {
  const WelcomeForm({Key? key}) : super(key: key);

  @override
  WelcomeFormState createState() => WelcomeFormState();
}

class InviteData {
  String? hub = '';
  String? coupon = '';
}

const String base28Digits = '23456789BCDFGHJKLMNPQRSTVWXZ';

class WelcomeFormState extends State<WelcomeForm> with RestorationMixin {
  // based on https://github.com/flutter/gallery/blob/d030f1e5316310c48fc725f619eb980a0597366d/lib/demos/material/text_field_demo.dart
  InviteData invite = InviteData();

  late FocusNode _hub, _coupon;

  @override
  void initState() {
    super.initState();
    _hub = FocusNode();
    _coupon = FocusNode();
  }

  @override
  void dispose() {
    _hub.dispose();
    _coupon.dispose();
    super.dispose();
  }

  void showInSnackBar(String value) {
    ScaffoldMessenger.of(context).hideCurrentSnackBar();
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(value),
    ));
  }

  @override
  String get restorationId => 'welcome_form';

  @override
  void restoreState(RestorationBucket? oldBucket, bool initialRestore) {
    registerForRestoration(_autoValidateModeIndex, 'autovalidate_mode');
  }

  final RestorableInt _autoValidateModeIndex =
      RestorableInt(AutovalidateMode.disabled.index);

  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  final _CouponTextInputFormatter _couponFormatter =
      _CouponTextInputFormatter();

  void _handleSubmitted() {
    final form = _formKey.currentState!;
    if (!form.validate()) {
      _autoValidateModeIndex.value =
          AutovalidateMode.always.index; // Start validating on every change.
      showInSnackBar("Please fix the errors in red before submitting.");
    } else {
      form.save();
      showInSnackBar("$invite.hub!:$invite.coupon!");
    }
  }

  String? _validateHub(String? value) {
    if (value == null || value.isEmpty) {
      return "Hub is required";
    }
    final hubExp = RegExp(r'^[^\.-].*\..*[^\.-]$');
    if (!hubExp.hasMatch(value)) {
      return "Hub must contain a '.' and begin and end with a letter or number.";
    }
    return null;
  }

  String? _validateCoupon(String? value) {
    if (value == null || value.isEmpty) {
      return "Coupon is required";
    }
    if (value.length != 18) {
      return "Coupon must be exactly 18 characters (including dashes).";
    }
    final hubExp = RegExp(r'^[' + base28Digits + r'-]{18}$');
    if (!hubExp.hasMatch(value)) {
      return "Please use only numbers and letters.";
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    const sizedBoxSpace = SizedBox(height: 24);

    return Form(
      key: _formKey,
      autovalidateMode: AutovalidateMode.values[_autoValidateModeIndex.value],
      child: Scrollbar(
        child: SingleChildScrollView(
          restorationId: 'text_field_demo_scroll_view',
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Column(
            children: [
              // center elements vertically if less than screen height
              Container(
                  height: // fixme: replace 700 with the actual height of widgets below
                      max((MediaQuery.of(context).size.height - 700) / 2, 0)),
              sizedBoxSpace,
              const FractionallySizedBox(
                widthFactor: 0.8,
                child: Text(
                  "Welcome to BitBurrow",
                  textAlign: TextAlign.center,
                  textScaleFactor: 1.8,
                  style: TextStyle(fontWeight: FontWeight.bold),
                ),
              ),
              sizedBoxSpace,
              FractionallySizedBox(
                widthFactor: 0.4,
                child: Image.asset("images/BitBurrow.png"),
              ),
              sizedBoxSpace,
              textMd(
                  context,
                  "This app needs a BitBurrow Hub to run. "
                  "Please enter the information below. "),
              sizedBoxSpace,
              TextFormField(
                restorationId: 'hub_field',
                textInputAction: TextInputAction.next,
                textCapitalization: TextCapitalization.none,
                decoration: InputDecoration(
                  filled: true,
                  icon: SvgPicture.asset(
                    'images/cloud-data-connection.svg',
                    width: 30,
                    height: 30,
                    color: Colors.grey[700],
                  ),
                  hintText: "example.com",
                  labelText: "Hub*",
                ),
                onSaved: (value) {
                  invite.hub = value;
                  _coupon.requestFocus();
                },
                validator: _validateHub,
                inputFormatters: <TextInputFormatter>[
                  // don't allow upper-case, common symbols except -.
                  FilteringTextInputFormatter.deny(RegExp(
                      r'''[A-Z~`!@#$%^&\*\(\)_\+=\[\]\{\}\|\\:;"'<>,/\? ]''')),
                ],
              ),
              sizedBoxSpace,
              TextFormField(
                restorationId: 'coupon_field',
                textInputAction: TextInputAction.next,
                textCapitalization: TextCapitalization.characters,
                decoration: InputDecoration(
                  filled: true,
                  icon: SvgPicture.asset(
                    'images/ticket.svg',
                    width: 30,
                    height: 30,
                    color: Colors.grey[700],
                  ),
                  hintText: "XXX-XXXX-XXX-XXXXX (Case-insensitive)",
                  labelText: "Coupon*",
                ),
                onSaved: (value) {
                  invite.coupon = value;
                },
                maxLength: 18,
                maxLengthEnforcement: MaxLengthEnforcement.none,
                validator: _validateCoupon,
                inputFormatters: <TextInputFormatter>[
                  _couponFormatter,
                ],
              ),
              sizedBoxSpace,
              Center(
                child: ElevatedButton(
                  onPressed: _handleSubmitted,
                  child: const Text("SUBMIT"),
                ),
              ),
              sizedBoxSpace,
              Text(
                "* indicates required field",
                style: Theme.of(context).textTheme.caption,
              ),
              sizedBoxSpace,
            ],
          ),
        ),
      ),
    );
  }
}

//                                                              123456789012345678
// Strip illegal chars, format incoming text to fit the format: XXX-XXXX-XXX-XXXXX
class _CouponTextInputFormatter extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
    var before = newValue.text.toUpperCase();
    var beforePos = newValue.selection.end;
    var beforeLength = before.length;
    final after = StringBuffer();
    var afterPos = 0;
    String unbackspaceTest =
        '${before.substring(0, beforePos)}-${before.substring(beforePos)}';
    if (unbackspaceTest == oldValue.text) {
      // if user backspaces over '-', delete the character before
      before =
          '${before.substring(0, beforePos - 1)}${before.substring(beforePos)}';
      beforePos = beforePos - 1;
      beforeLength = before.length;
    }
    for (int i = 0; i < beforeLength; i++) {
      if (i == beforePos) afterPos = after.length;
      var c = before[i];
      if (base28Digits.contains(c)) after.write(c);
      var l = after.length;
      if (l == 3 || l == 8 || l == 12) after.write('-');
    }
    if (beforeLength == beforePos) afterPos = after.length;
    return TextEditingValue(
      text: after.toString(),
      selection: TextSelection.collapsed(offset: afterPos),
    );
  }
}

class Page2Screen extends StatelessWidget {
  const Page2Screen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => ourScreenLayout(
        context,
        textMd(context, text1 + text2 * 15 + text3),
      );
}

class Page3Screen extends StatelessWidget {
  const Page3Screen({Key? key}) : super(key: key);

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text(App.title)),
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              ElevatedButton(
                onPressed: () => context.go('/'),
                child: const Text('Go back to home page'),
              ),
            ],
          ),
        ),
      );
}
