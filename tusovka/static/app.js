// Невеликі покращення UX. Сайт повністю працює і без JavaScript.
document.querySelectorAll("[data-copy]").forEach(function (btn) {
  var target = document.querySelector(btn.getAttribute("data-copy"));
  if (!target || !navigator.clipboard) return;
  btn.hidden = false;
  var label = btn.innerHTML;
  btn.addEventListener("click", function () {
    navigator.clipboard.writeText(target.value).then(function () {
      btn.textContent = "Скопійовано ✓";
      setTimeout(function () { btn.innerHTML = label; }, 2000);
    });
  });
});

document.querySelectorAll("input.copy").forEach(function (input) {
  input.addEventListener("focus", function () { input.select(); });
});

// Після «Назад» браузер може показати сторінку з кешу — знову вмикаємо кнопки
window.addEventListener("pageshow", function () {
  document.querySelectorAll("button[type=submit][disabled]").forEach(function (b) { b.disabled = false; });
});

// Захист від подвійного надсилання форм (подвійний клік на «Іду», «Надіслати»)
document.querySelectorAll("form[method=post]").forEach(function (form) {
  form.addEventListener("submit", function () {
    var btn = form.querySelector("button[type=submit]");
    if (btn) setTimeout(function () { btn.disabled = true; }, 0);
  });
});
