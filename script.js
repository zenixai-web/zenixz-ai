document.addEventListener('DOMContentLoaded', () => {

    // --- High Performance Scroll Reveal ---
    const revealElements = document.querySelectorAll('.reveal-on-scroll');

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
            }
        });
    }, {
        threshold: 0.15
    });

    revealElements.forEach(el => observer.observe(el));

});
document.addEventListener('keydown', function(e) {
    // F12 කී එක
    if (e.key === 'F12') {
        e.preventDefault();
    }
    // Ctrl+Shift+I හෝ Ctrl+Shift+J හෝ Ctrl+U බ්ලොක් කිරීමට
    if (e.ctrlKey && e.shiftKey && (e.key === 'I' || e.key === 'J' || e.key === 'C')) {
        e.preventDefault();
    }
    if (e.ctrlKey && e.key === 'u') {
        e.preventDefault();
    }
});