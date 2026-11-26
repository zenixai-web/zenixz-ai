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

// --- Block Right-Click & Inspect / Developer Tools ---
document.addEventListener('contextmenu', function(e) {
    e.preventDefault();
});

document.addEventListener('keydown', function(e) {
    // F12
    if (e.key === 'F12' || e.keyCode === 123) {
        e.preventDefault();
        return false;
    }
    // Ctrl+Shift+I / J / C / K
    if (e.ctrlKey && e.shiftKey && ['I', 'J', 'C', 'K', 'i', 'j', 'c', 'k'].includes(e.key)) {
        e.preventDefault();
        return false;
    }
    // Cmd+Option+I / J / C (Mac)
    if (e.metaKey && e.altKey && ['I', 'J', 'C', 'i', 'j', 'c'].includes(e.key)) {
        e.preventDefault();
        return false;
    }
    // Ctrl+U / Cmd+U (View Source)
    if ((e.ctrlKey || e.metaKey) && ['U', 'u', 'S', 's'].includes(e.key)) {
        e.preventDefault();
        return false;
    }
});

// --- Smooth Scroll-Synced Growth Curve Animation ---
window.addEventListener('scroll', () => {
    const chartBox = document.getElementById('growthChartBox');
    const curvePath = document.getElementById('hdCurvePath');
    
    if (chartBox && curvePath) {
        const rect = chartBox.getBoundingClientRect();
        const windowHeight = window.innerHeight;
        
        let scrollFraction = (windowHeight - rect.top) / (windowHeight + rect.height * 0.6);
        scrollFraction = Math.max(0, Math.min(1, scrollFraction));
        
        const pathLength = 1000;
        const drawLength = pathLength * scrollFraction;
        
        curvePath.style.strokeDashoffset = pathLength - drawLength;
    }
});

// --- Optimized Scroll-Synced Bar Chart Animation for Mobiles & Desktop ---
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