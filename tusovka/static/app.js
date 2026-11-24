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

// Обкладинка: перевіряємо розмір і тип ще до відправки, показуємо прев'ю
document.querySelectorAll("input[type=file][data-max-bytes]").forEach(function (input) {
  var max = parseInt(input.getAttribute("data-max-bytes"), 10);
  var msg = document.createElement("p");
  msg.className = "field-error";
  msg.hidden = true;
  input.insertAdjacentElement("afterend", msg);
  var preview = document.createElement("img");
  preview.className = "cover-preview";
  preview.alt = "Прев'ю обкладинки";
  preview.hidden = true;
  msg.insertAdjacentElement("afterend", preview);
  input.addEventListener("change", function () {
    var f = input.files && input.files[0];
    msg.hidden = true;
    preview.hidden = true;
    input.setCustomValidity("");
    if (!f) return;
    var problem = null;
    if (f.size > max) problem = "Файл завеликий — максимум 5 МБ.";
    else if (["image/jpeg", "image/png", "image/webp"].indexOf(f.type) === -1) problem = "Підтримуються лише JPEG, PNG або WebP.";
    if (problem) {
      msg.textContent = problem;
      msg.hidden = false;
      input.value = "";
      return;
    }
    preview.src = URL.createObjectURL(f);
    preview.hidden = false;
  });
});

// Лічильник символів для полів з обмеженням довжини
document.querySelectorAll("textarea[maxlength]").forEach(function (ta) {
  var max = parseInt(ta.getAttribute("maxlength"), 10);
  var counter = document.createElement("span");
  counter.className = "char-counter muted small";
  ta.insertAdjacentElement("afterend", counter);
  function update() { counter.textContent = ta.value.length + " / " + max; }
  ta.addEventListener("input", update);
  update();
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
