// Chittak — Android va iOS ilova. chittak.uz saytidagi Mini App'ni WebView ichida ochadi.
//
// Telegram ichida bo'lmagani uchun kirish bot orqali tasdiqlanadi (sayt o'zi
// "Telegram orqali kirish" sahifasini ko'rsatadi). Ilova faqat:
//  - user-agent'ga "TestWayApp" qo'shadi (sayt shu bo'yicha ilova ekanini biladi);
//  - t.me va boshqa tashqi havolalarni Telegram/brauzerda ochadi;
//  - Android "orqaga" tugmasini saytdagi "←" ga ulaydi;
//  - internet yo'q bo'lsa, qayta urinish oynasini ko'rsatadi;
//  - jonli skaner uchun kameraga ruxsat beradi (sayt so'raganda).

import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:url_launcher/url_launcher.dart';

const appUrl = 'https://www.chittak.uz/';
final appHost = Uri.parse(appUrl).host;
// chittak.uz o'zi www.chittak.uz ga yo'naltiradi — ikkalasi ham o'z saytimiz
const ownHosts = {'www.chittak.uz', 'chittak.uz'};

const _navy = Color(0xFF0B1F2A);
const _background = Color(0xFFF3FAF5);

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const TestWayApp());
}

class TestWayApp extends StatelessWidget {
  const TestWayApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Chittak',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: _navy, scaffoldBackgroundColor: _background),
      home: const WebShell(),
    );
  }
}

class WebShell extends StatefulWidget {
  const WebShell({super.key});

  @override
  State<WebShell> createState() => _WebShellState();
}

class _WebShellState extends State<WebShell> {
  InAppWebViewController? _controller;
  double _progress = 0;
  bool _offline = false;
  // Sayt birinchi marta ko'ringuncha brend ochilish ekrani turadi (oq ekran miltillamaydi)
  bool _firstPaint = false;
  // Ochilish ekrani har kirishda kamida 3 soniya ko'rinadi
  bool _splashTimeUp = false;
  Timer? _splashTimer;

  bool get _splashDone => _firstPaint && _splashTimeUp;

  @override
  void initState() {
    super.initState();
    _splashTimer = Timer(const Duration(seconds: 3), () {
      if (mounted) setState(() => _splashTimeUp = true);
    });
  }

  @override
  void dispose() {
    _splashTimer?.cancel();
    super.dispose();
  }

  Future<void> _openExternal(WebUri uri) async {
    final target = Uri.parse(uri.toString());
    var opened = false;
    try {
      opened = await launchUrl(target, mode: LaunchMode.externalApplication);
    } catch (_) {
      opened = false;
    }
    if (opened) return;
    // Mos ilova yo'q (masalan Telegram o'rnatilmagan): tg://resolve?domain=x -> https://t.me/x brauzerda
    var fallback = target;
    if (target.scheme == 'tg') {
      final domain = target.queryParameters['domain'];
      if (domain == null) return;
      final start = target.queryParameters['start'];
      fallback = Uri.https('t.me', '/$domain', start == null ? null : {'start': start});
    }
    try {
      await launchUrl(fallback, mode: LaunchMode.platformDefault);
    } catch (_) {
      // Ochib bo'lmadi — sahifada qolamiz
    }
  }

  Future<void> _handleBack() async {
    final handled = await _controller?.evaluateJavascript(
      source: 'window.appBack ? window.appBack() : false',
    );
    if (handled == true) return;
    await SystemNavigator.pop();
  }

  void _retry() {
    setState(() => _offline = false);
    _controller?.reload();
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) _handleBack();
      },
      child: Scaffold(
        // Ochilish ekrani paytida tepa (status bar) ham to'q bo'lsin
        backgroundColor: _splashDone ? _background : _splashBg,
        body: SafeArea(
          child: Stack(
            children: [
              InAppWebView(
                initialUrlRequest: URLRequest(url: WebUri(appUrl)),
                initialSettings: InAppWebViewSettings(
                  applicationNameForUserAgent: 'TestWayApp/1.0',
                  javaScriptEnabled: true,
                  domStorageEnabled: true,
                  useShouldOverrideUrlLoading: true,
                  supportMultipleWindows: true,
                  javaScriptCanOpenWindowsAutomatically: true,
                  transparentBackground: true,
                  mediaPlaybackRequiresUserGesture: false,
                  allowsInlineMediaPlayback: true,
                  supportZoom: false,
                ),
                onWebViewCreated: (controller) => _controller = controller,
                // Faqat o'z saytimiz ilova ichida; qolgani (t.me, Payme, Click) — tashqarida
                shouldOverrideUrlLoading: (controller, action) async {
                  final uri = action.request.url;
                  if (uri == null) return NavigationActionPolicy.ALLOW;
                  // Sahifa ichidagi kadr (masalan, video darsdagi YouTube pleyeri) — tashqariga chiqarilmaydi
                  if (!action.isForMainFrame) return NavigationActionPolicy.ALLOW;
                  final isOwnSite = ownHosts.contains(uri.host) && (uri.scheme == 'https' || uri.scheme == 'http');
                  final isInternal = uri.scheme == 'about' || uri.scheme == 'blob' || uri.scheme == 'data';
                  if (isOwnSite || isInternal) return NavigationActionPolicy.ALLOW;
                  await _openExternal(uri);
                  return NavigationActionPolicy.CANCEL;
                },
                // window.open(...) — yangi oyna o'rniga tashqi ilova
                onCreateWindow: (controller, action) async {
                  final uri = action.request.url;
                  if (uri != null) await _openExternal(uri);
                  return false;
                },
                // Jonli skaner kamerani so'raydi — faqat o'z saytimizga beriladi. Android'da ruxsat shu yerda
                // so'raladi; iOS'da tizimning o'zi so'raydi (Info.plist'dagi NSCameraUsageDescription matni bilan)
                onPermissionRequest: (controller, request) async {
                  final wantsCamera = request.resources.contains(PermissionResourceType.CAMERA);
                  final ownSite = ownHosts.contains(request.origin.host);
                  final granted = wantsCamera &&
                      ownSite &&
                      (Platform.isIOS || (await Permission.camera.request()).isGranted);
                  return PermissionResponse(
                    resources: request.resources,
                    action: granted ? PermissionResponseAction.GRANT : PermissionResponseAction.DENY,
                  );
                },
                onProgressChanged: (controller, progress) => setState(() => _progress = progress / 100),
                // Sahifa ko'rinishi bilan chiziq yo'qoladi — tashqi skript/shrift sekin yuklansa ham qolib ketmaydi
                onPageCommitVisible: (controller, url) => setState(() {
                  _progress = 1;
                  _firstPaint = true;
                }),
                onLoadStop: (controller, url) => setState(() {
                  _progress = 1;
                  _firstPaint = true;
                }),
                onReceivedError: (controller, request, error) {
                  if (request.isForMainFrame ?? true) setState(() => _offline = true);
                },
              ),
              if (_progress < 1 && _splashDone) LinearProgressIndicator(value: _progress, minHeight: 3, color: _navy),
              IgnorePointer(
                ignoring: _splashDone || _offline,
                child: AnimatedOpacity(
                  opacity: _splashDone || _offline ? 0 : 1,
                  duration: const Duration(milliseconds: 350),
                  child: const _SplashView(),
                ),
              ),
              if (_offline) _OfflineView(onRetry: _retry),
            ],
          ),
        ),
      ),
    );
  }
}

class _OfflineView extends StatelessWidget {
  const _OfflineView({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Container(
      color: _background,
      padding: const EdgeInsets.all(32),
      alignment: Alignment.center,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text('📡', style: TextStyle(fontSize: 56)),
          const SizedBox(height: 16),
          const Text(
            'Internet bilan aloqa yo\'q',
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.w800, color: _navy),
          ),
          const SizedBox(height: 8),
          const Text(
            'Internetni tekshirib, qayta urinib ko\'ring.',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.black54),
          ),
          const SizedBox(height: 24),
          FilledButton(
            onPressed: onRetry,
            style: FilledButton.styleFrom(backgroundColor: _navy, minimumSize: const Size(200, 48)),
            child: const Text('Qayta urinish'),
          ),
        ],
      ),
    );
  }
}


// Ochilish ekrani ("Zinapoya") — saytdagi (frontend/index.html) bilan bir xil: to'q yashil fon, to'rtta
// o'sib chiquvchi pog'ona, qush ulardan sakrab yuqoriga chiqadi (qo'ngan pog'ona yashil yonadi), keyin
// "Chittak" va shior ko'tariladi. Bu yerda yopilishda shunchaki so'nadi: ostida saytdagi xuddi shu ekran
// turadi va u o'zi qushni uchirib yopiladi (ikkalasi uchsa, qush ikki marta uchib ketgandek ko'rinardi).
const _splashBg = Color(0xFF033331);
const _lime = Color(0xFF3EDC7E);
// CSS'dagi cubic-bezier(.22,1,.36,1)
const _outQuint = Cubic(0.22, 1, 0.36, 1);

class _SplashView extends StatefulWidget {
  const _SplashView();

  @override
  State<_SplashView> createState() => _SplashViewState();
}

// Qushning bir lahzadagi holati: (x, y) — "u" birligida (ekran kengligining 1% i), sx/sy — cho'zilish
class _HopKey {
  const _HopKey(this.t, this.x, this.y, {this.sx = 1, this.sy = 1, this.opacity = 1, this.curve = Curves.linear});

  final double t, x, y, sx, sy, opacity;
  final Curve curve; // shu nuqtadan keyingisigacha bo'lgan harakat egri chizig'i

  _HopKey lerp(_HopKey b, double t) {
    final k = curve.transform(((t - this.t) / (b.t - this.t)).clamp(0.0, 1.0));
    double mix(double p, double q) => p + (q - p) * k;
    return _HopKey(t, mix(x, b.x), mix(y, b.y), sx: mix(sx, b.sx), sy: mix(sy, b.sy), opacity: mix(opacity, b.opacity));
  }
}

// Saytdagi @keyframes boot-hop bilan bir xil: har pog'ona markaziga (18u qadam, 10u balandlik) qo'nadi,
// ko'tarilish easeOut, tushish easeIn; oxirgi pog'onada bir oz "yassilanadi"
const _hopKeys = [
  _HopKey(0, -1, -10, sx: .3, sy: .3, opacity: 0, curve: Curves.easeOut),
  _HopKey(.12, -1, -10, curve: Curves.easeOut),
  _HopKey(.20, 8, -30, curve: Curves.easeIn),
  _HopKey(.32, 17, -20, curve: Curves.easeOut),
  _HopKey(.44, 26, -40, curve: Curves.easeIn),
  _HopKey(.56, 35, -30, curve: Curves.easeOut),
  _HopKey(.68, 44, -50, curve: Curves.easeIn),
  _HopKey(.80, 53, -40, curve: Curves.easeOut),
  _HopKey(.88, 53, -40, sx: 1.12, sy: .86),
  _HopKey(1, 53, -40),
];

_HopKey _hopAt(double t) {
  for (var i = 0; i < _hopKeys.length - 1; i++) {
    if (t <= _hopKeys[i + 1].t) return _hopKeys[i].lerp(_hopKeys[i + 1], t);
  }
  return _hopKeys.last;
}

class _SplashViewState extends State<_SplashView> with TickerProviderStateMixin {
  // Butun kirish 1,95 soniya. Quyidagi vaqtlar (ms) saytdagi CSS'dagi delay/duration bilan bir xil.
  static const _totalMs = 1950;
  static const _hopStartMs = 450, _hopMs = 1300;
  static const _litMs = [600, 870, 1180, 1490]; // qush har pog'onaga qo'nadigan payt

  late final AnimationController _intro = AnimationController(vsync: this, duration: const Duration(milliseconds: _totalMs))
    ..forward();
  late final AnimationController _breathe = AnimationController(vsync: this, duration: const Duration(milliseconds: 1600))
    ..repeat(reverse: true);

  @override
  void dispose() {
    _intro.dispose();
    _breathe.dispose();
    super.dispose();
  }

  // [startMs, startMs + durMs] oralig'ida 0→1
  Animation<double> _at(int startMs, int durMs, Curve curve) => CurvedAnimation(
        parent: _intro,
        curve: Interval(startMs / _totalMs, ((startMs + durMs) / _totalMs).clamp(0.0, 1.0), curve: curve),
      );

  Widget _step(int i, double u) {
    final grow = _at(50 + 80 * i, 550, _outQuint);
    final lit = _at(_litMs[i], 350, Curves.ease);
    final radius = BorderRadius.vertical(top: Radius.circular(2.5 * u));
    return AnimatedBuilder(
      animation: _intro,
      builder: (_, _) => Transform(
        alignment: Alignment.bottomCenter,
        transform: Matrix4.diagonal3Values(1, grow.value, 1),
        child: Stack(
          fit: StackFit.expand,
          children: [
            DecoratedBox(
              decoration: BoxDecoration(
                borderRadius: radius,
                gradient: const LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Color(0x993EDC7E), Color(0x0F00A844)],
                ),
              ),
            ),
            Opacity(
              opacity: lit.value,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  borderRadius: radius,
                  gradient: const LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [_lime, Color(0x2600A844)],
                  ),
                  boxShadow: [BoxShadow(color: const Color(0x8C3EDC7E), blurRadius: 6 * u)],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final size = MediaQuery.of(context).size;
    final u = (size.width < 420 ? size.width : 420) / 100; // saytdagi --u
    final glowSize = size.longestSide * 0.85;
    final titleSize = (size.width * 0.11).clamp(38.0, 52.0);
    final sloganSize = (size.width * 0.046).clamp(16.0, 20.0);
    final glow = _at(0, 1400, Curves.easeOut);
    final title = _at(1200, 600, _outQuint);
    final slogan = _at(1350, 600, _outQuint);

    Widget rise(Animation<double> a, Widget child) => AnimatedBuilder(
          animation: a,
          builder: (context, _) => Opacity(
            opacity: a.value.clamp(0.0, 1.0),
            child: Transform.translate(offset: Offset(0, 14 * (1 - a.value)), child: child),
          ),
        );

    return Container(
      decoration: const BoxDecoration(
        gradient: RadialGradient(
          center: Alignment(0, 0.05),
          radius: 1.1,
          colors: [Color(0xFF0A4537), Color(0xFF05362D), Color(0xFF032B27)],
          stops: [0, 0.45, 1],
        ),
      ),
      child: Stack(
        children: [
          // O'ng yuqori burchakdan tushayotgan yashil nur: kirishda yonadi, keyin sekin "nafas oladi"
          Positioned(
            right: -glowSize * 0.42,
            top: -glowSize * 0.42,
            child: AnimatedBuilder(
              animation: Listenable.merge([glow, _breathe]),
              builder: (context, _) => Opacity(
                opacity: glow.value * (1 - 0.28 * _breathe.value),
                child: Container(
                  width: glowSize,
                  height: glowSize,
                  decoration: const BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: RadialGradient(
                      colors: [Color(0x9400C853), Color(0x3800C853), Color(0x0000C853)],
                      stops: [0, 0.34, 0.66],
                    ),
                  ),
                ),
              ),
            ),
          ),
          Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  width: 72 * u,
                  height: 62 * u,
                  child: Stack(
                    clipBehavior: Clip.none,
                    children: [
                      for (var i = 0; i < 4; i++)
                        Positioned(
                          left: i * 18 * u,
                          bottom: 0,
                          width: 16 * u,
                          height: (i + 1) * 10 * u,
                          child: _step(i, u),
                        ),
                      Positioned(
                        left: 0,
                        bottom: 0,
                        width: 18 * u,
                        child: AnimatedBuilder(
                          animation: _intro,
                          builder: (_, child) {
                            final ms = _intro.value * _totalMs;
                            final k = _hopAt(((ms - _hopStartMs) / _hopMs).clamp(0.0, 1.0));
                            return Opacity(
                              opacity: k.opacity.clamp(0.0, 1.0),
                              child: Transform.translate(
                                offset: Offset(k.x * u, k.y * u),
                                child: Transform(
                                  alignment: Alignment.center,
                                  transform: Matrix4.diagonal3Values(k.sx, k.sy, 1),
                                  child: child,
                                ),
                              ),
                            );
                          },
                          child: Image.asset('assets/splash_bird.png'),
                        ),
                      ),
                    ],
                  ),
                ),
                SizedBox(height: 7 * u),
                rise(
                  title,
                  Text(
                    'Chittak',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: titleSize,
                      fontWeight: FontWeight.w800,
                      letterSpacing: -0.045 * titleSize,
                      height: 1.05,
                    ),
                  ),
                ),
                const SizedBox(height: 10),
                rise(
                  slogan,
                  Text(
                    "Ilg'or ta'lim,\ntezkor natija",
                    textAlign: TextAlign.center,
                    style: TextStyle(color: _lime, fontSize: sloganSize, fontWeight: FontWeight.w700, height: 1.3),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
