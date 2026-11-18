/* Accesibilidad: nombre para los enlaces de paginación que solo llevan un
   icono (CKAN genera las flechas en Python, sin texto: un lector de pantalla
   no sabía qué hacían). */
document.addEventListener('DOMContentLoaded', function () {
  var labels = {'fa-chevron-right': 'Página siguiente', 'fa-chevron-left': 'Página anterior'};
  document.querySelectorAll('.pagination a.page-link').forEach(function (a) {
    if (a.textContent.trim() || a.getAttribute('aria-label')) { return; }
    Object.keys(labels).forEach(function (cls) {
      if (a.querySelector('i.' + cls)) {
        a.setAttribute('aria-label', labels[cls]);
        a.setAttribute('title', labels[cls]);
      }
    });
  });
});
