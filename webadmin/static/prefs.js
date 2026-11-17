"use strict";
// 動畫偏好要在畫面出來前決定（CSP 不允許內嵌 script，所以獨立一個檔）。
// 自己選過就照選的；沒選過就跟系統的「減少動態效果」。html.rm＝簡化動畫，html.rm-sys＝是系統設定造成的（會提示可以打開）。
(function () {
  var saved = null; try { saved = localStorage.getItem("motion"); } catch (e) { /* 讀不到就跟系統 */ }
  var sys = matchMedia("(prefers-reduced-motion: reduce)").matches, cl = document.documentElement.classList;
  if (saved === "off" || (saved !== "on" && sys)) cl.add("rm");
  if (sys && saved !== "on" && saved !== "off") cl.add("rm-sys");
})();
