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
// Smooth Scroll-Synced Growth Curve Animation
window.addEventListener('scroll', () => {
    const chartBox = document.getElementById('growthChartBox');
    const curvePath = document.getElementById('hdCurvePath');
    
    if (chartBox && curvePath) {
        const rect = chartBox.getBoundingClientRect();
        const windowHeight = window.innerHeight;
        
        // Calculate scroll ratio relative to element position
        let scrollFraction = (windowHeight - rect.top) / (windowHeight + rect.height * 0.6);
        scrollFraction = Math.max(0, Math.min(1, scrollFraction));
        
        const pathLength = 1000;
        const drawLength = pathLength * scrollFraction;
        
        // Dynamic Offset based on Scroll Position
        curvePath.style.strokeDashoffset = pathLength - drawLength;
    }
});
// Optimized Scroll-Synced Bar Chart Animation for Mobiles
let isTicking = false;

window.addEventListener('scroll', () => {
    if (!isTicking) {
        window.requestAnimationFrame(() => {
            const chartBox = document.getElementById('growthChartBox');
            
            if (chartBox) {
                const rect = chartBox.getBoundingClientRect();
                const windowHeight = window.innerHeight;
                
                let scrollFraction = (windowHeight - rect.top) / (windowHeight + rect.height * 0.5);
                scrollFraction = Math.max(0, Math.min(1, scrollFraction));
                
                const bars = chartBox.querySelectorAll('.bar-fill');
                bars.forEach(bar => {
                    const targetHeightPercent = parseFloat(bar.getAttribute('data-height'));
                    const currentHeight = targetHeightPercent * scrollFraction;
                    bar.style.height = currentHeight + '%';
                    
                    if (scrollFraction > 0.4) {
                        bar.classList.add('active-value');
                    } else {
                        bar.classList.remove('active-value');
                    }
                });
            }
            isTicking = false;
        });
        isTicking = true;
    }
});