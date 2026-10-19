// Chittak — Android ilova. Render'dagi Mini App'ni WebView ichida ochadi.
//
// Telegram ichida bo'lmagani uchun kirish bot orqali tasdiqlanadi (sayt o'zi
// "Telegram orqali kirish" sahifasini ko'rsatadi). Ilova faqat:
//  - user-agent'ga "TestWayApp" qo'shadi (sayt shu bo'yicha ilova ekanini biladi);
//  - t.me va boshqa tashqi havolalarni Telegram/brauzerda ochadi;
//  - Android "orqaga" tugmasini saytdagi "←" ga ulaydi;
//  - internet yo'q bo'lsa, qayta urinish oynasini ko'rsatadi;
//  - jonli skaner uchun kameraga ruxsat beradi (sayt so'raganda).

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_inappwebview/flutter_inappwebview.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:url_launcher/url_launcher.dart';

const appUrl = 'https://bo-tlarim.onrender.com/';
final appHost = Uri.parse(appUrl).host;

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
    try {
      await launchUrl(Uri.parse(uri.toString()), mode: LaunchMode.externalApplication);
    } catch (_) {
      // Telegram o'rnatilmagan bo'lsa — havola brauzerda ochiladi
      if (uri.scheme == 'tg') return;
      await launchUrl(Uri.parse(uri.toString()), mode: LaunchMode.platformDefault);
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
                  final isOwnSite = uri.host == appHost && (uri.scheme == 'https' || uri.scheme == 'http');
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
                // Jonli skaner kamerani so'raydi — Android ruxsati olinib, faqat o'z saytimizga beriladi
                onPermissionRequest: (controller, request) async {
                  final wantsCamera = request.resources.contains(PermissionResourceType.CAMERA);
                  final ownSite = request.origin.host == appHost;
                  final granted = wantsCamera && ownSite && (await Permission.camera.request()).isGranted;
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


// Ochilish ekrani — Mini App'dagi bilan bir xil: to'q yashil fon, qush, "Chittak", shior va
// burchakdagi yashil to'lqinlar. Qush sakrab tushadi, yozuvlar ko'tariladi, to'lqinlar suzib kiradi.
const _splashBg = Color(0xFF033331);

class _SplashView extends StatefulWidget {
  const _SplashView();

  @override
  State<_SplashView> createState() => _SplashViewState();
}

class _SplashViewState extends State<_SplashView> with TickerProviderStateMixin {
  late final AnimationController _intro = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))
    ..forward();
  late final AnimationController _float = AnimationController(vsync: this, duration: const Duration(milliseconds: 1300))
    ..repeat(reverse: true);

  @override
  void dispose() {
    _intro.dispose();
    _float.dispose();
    super.dispose();
  }

  // Bosqich: [start, end] oralig'ida 0→1 (umumiy 1,2 soniyalik kirish ichida)
  Animation<double> _step(double start, double end, Curve curve) =>
      CurvedAnimation(parent: _intro, curve: Interval(start, end, curve: curve));

  @override
  Widget build(BuildContext context) {
    final bird = _step(0.0, 0.75, Curves.elasticOut);
    final birdFade = _step(0.0, 0.3, Curves.easeOut);
    final title = _step(0.3, 0.8, Curves.easeOutCubic);
    final slogan = _step(0.45, 0.95, Curves.easeOutCubic);
    final waves = _step(0.15, 1.0, Curves.easeOutCubic);
    final glow = _step(0.0, 1.0, Curves.easeOut);
    final size = MediaQuery.of(context).size;
    final width = size.width;
    final glowSize = size.longestSide * 0.85;

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
              animation: Listenable.merge([glow, _float]),
              builder: (context, _) => Opacity(
                opacity: glow.value * (1 - 0.28 * _float.value),
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
          Positioned(
            right: 0,
            bottom: 0,
            child: AnimatedBuilder(
              animation: waves,
              builder: (_, child) => Opacity(
                opacity: waves.value,
                child: Transform.translate(offset: Offset(60 * (1 - waves.value), 50 * (1 - waves.value)), child: child),
              ),
              child: CustomPaint(size: Size(width * 0.78, width * 0.78 * 260 / 420), painter: _WavesPainter()),
            ),
          ),
          Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                AnimatedBuilder(
                  animation: Listenable.merge([bird, _float]),
                  builder: (_, child) => Opacity(
                    opacity: birdFade.value,
                    child: Transform.translate(
                      offset: Offset(0, -38 * (1 - bird.value) - 6 * _float.value),
                      child: Transform.scale(scale: 0.82 + 0.18 * bird.value, child: child),
                    ),
                  ),
                  child: Image.asset('assets/splash_bird.png', width: 180),
                ),
                const SizedBox(height: 8),
                rise(
                  title,
                  const Text(
                    'Chittak',
                    style: TextStyle(color: Colors.white, fontSize: 46, fontWeight: FontWeight.w800, letterSpacing: -2),
                  ),
                ),
                const SizedBox(height: 10),
                rise(
                  slogan,
                  const Text(
                    "Ilg'or ta'lim,\ntezkor natija",
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Color(0xFF3EDC7E), fontSize: 19, fontWeight: FontWeight.w700, height: 1.3),
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

// O'ng pastdagi ikki qavatli yashil to'lqin (Mini App'dagi SVG bilan bir xil shakl, 420x260 o'lchamda chizilgan)
class _WavesPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    canvas.scale(size.width / 420, size.height / 260);
    final back = Path()
      ..moveTo(420, 40)
      ..cubicTo(330, 70, 250, 120, 190, 190)
      ..cubicTo(160, 225, 120, 250, 70, 260)
      ..lineTo(420, 260)
      ..close();
    final front = Path()
      ..moveTo(420, 120)
      ..cubicTo(350, 140, 290, 180, 250, 230)
      ..cubicTo(240, 243, 228, 253, 214, 260)
      ..lineTo(420, 260)
      ..close();
    canvas.drawPath(back, Paint()..color = const Color(0xFF0C6340));
    canvas.drawPath(
      front,
      Paint()
        ..shader = const LinearGradient(colors: [Color(0xFF2FB862), Color(0xFF00A844)])
            .createShader(const Rect.fromLTWH(214, 120, 206, 140)),
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
