// CJK font fallback for cross-OS fingerprinting.
// Maps platform-specific CJK font names to universally available alternatives,
// so Chinese/Japanese/Korean text renders correctly regardless of host OS vs fingerprint OS.
(function () {
  var css = [
    // Windows → macOS / Linux fallback
    "@font-face{font-family:'Microsoft YaHei';src:local('Microsoft YaHei'),local('PingFang SC'),local('Hiragino Sans GB'),local('Noto Sans CJK SC'),local('WenQuanYi Micro Hei'),local('sans-serif')}",
    "@font-face{font-family:'\\5FAE\\8F6F\\96C5\\9ED1';src:local('\\5FAE\\8F6F\\96C5\\9ED1'),local('PingFang SC'),local('Hiragino Sans GB'),local('Noto Sans CJK SC'),local('WenQuanYi Micro Hei')}",
    "@font-face{font-family:'SimSun';src:local('SimSun'),local('Songti SC'),local('STSong'),local('Noto Serif CJK SC'),local('AR PL UMing CN')}",
    "@font-face{font-family:'\\5B8B\\4F53';src:local('\\5B8B\\4F53'),local('Songti SC'),local('STSong'),local('Noto Serif CJK SC'),local('AR PL UMing CN')}",
    "@font-face{font-family:'SimHei';src:local('SimHei'),local('PingFang SC'),local('Heiti SC'),local('Noto Sans CJK SC'),local('WenQuanYi Zen Hei')}",
    "@font-face{font-family:'\\9ED1\\4F53';src:local('\\9ED1\\4F53'),local('PingFang SC'),local('Heiti SC'),local('Noto Sans CJK SC'),local('WenQuanYi Zen Hei')}",
    "@font-face{font-family:'KaiTi';src:local('KaiTi'),local('Kaiti SC'),local('STKaiti'),local('Noto Sans CJK SC'),local('AR PL UKai CN')}",
    "@font-face{font-family:'\\6977\\4F53';src:local('\\6977\\4F53'),local('Kaiti SC'),local('STKaiti'),local('Noto Sans CJK SC'),local('AR PL UKai CN')}",
    // macOS → Windows / Linux fallback
    "@font-face{font-family:'PingFang SC';src:local('PingFang SC'),local('Microsoft YaHei'),local('Noto Sans CJK SC'),local('WenQuanYi Micro Hei')}",
    "@font-face{font-family:'Hiragino Sans GB';src:local('Hiragino Sans GB'),local('Microsoft YaHei'),local('Noto Sans CJK SC'),local('WenQuanYi Micro Hei')}",
    "@font-face{font-family:'Heiti SC';src:local('Heiti SC'),local('Microsoft YaHei'),local('SimHei'),local('Noto Sans CJK SC')}",
    // Linux → Windows / macOS fallback
    "@font-face{font-family:'Noto Sans CJK SC';src:local('Noto Sans CJK SC'),local('PingFang SC'),local('Microsoft YaHei'),local('WenQuanYi Micro Hei')}",
    "@font-face{font-family:'WenQuanYi Micro Hei';src:local('WenQuanYi Micro Hei'),local('PingFang SC'),local('Microsoft YaHei'),local('Noto Sans CJK SC')}"
  ].join("\n");
  var style = document.createElement("style");
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);
})();
