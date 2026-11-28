(function () {
    "use strict";

    function initNavigationAndDropdown() {
        const mobileMenuBtn =
            document.getElementById("mobileMenuBtn") ||
            document.querySelector(".mobile-menu-toggle");
        const menuIcon =
            document.getElementById("menuIcon") ||
            (mobileMenuBtn ? mobileMenuBtn.querySelector("i") : null);
        const navMenu =
            document.getElementById("navMenu") ||
            document.querySelector(".nav-menu");
        const dropdownToggle =
            document.getElementById("dropdownToggle") ||
            document.querySelector(".dropdown-link");
        const servicesDropdown =
            document.getElementById("servicesDropdown") ||
            document.querySelector(".nav-item.dropdown");
        const arrowIcon =
            document.getElementById("arrowIcon") ||
            (dropdownToggle ? dropdownToggle.querySelector(".dropdown-icon") : null);

        function setDrawerOpen(isOpen) {
            if (!navMenu) return;
            navMenu.classList.toggle("active", isOpen);
            if (mobileMenuBtn) {
                mobileMenuBtn.setAttribute("aria-expanded", String(isOpen));
            }
            if (menuIcon) {
                menuIcon.classList.toggle("fa-xmark", isOpen);
                menuIcon.classList.toggle("fa-bars", !isOpen);
            }
        }

        function setDropdownOpen(isOpen) {
            if (!servicesDropdown) return;
            servicesDropdown.classList.toggle("active", isOpen);
            if (arrowIcon) {
                arrowIcon.style.transform = isOpen ? "rotate(180deg)" : "rotate(0deg)";
            }
        }

        if (mobileMenuBtn && navMenu) {
            mobileMenuBtn.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                const nextState = !navMenu.classList.contains("active");
                setDrawerOpen(nextState);
                if (!nextState) setDropdownOpen(false);
            });
        }

        if (dropdownToggle && servicesDropdown) {
            dropdownToggle.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                const nextState = !servicesDropdown.classList.contains("active");
                setDropdownOpen(nextState);
            });
        }

        if (navMenu) {
            navMenu.querySelectorAll("a").forEach(function (link) {
                if (link === dropdownToggle || link.classList.contains("dropdown-link")) {
                    return;
                }
                link.addEventListener("click", function () {
                    setDropdownOpen(false);
                    setDrawerOpen(false);
                });
            });
        }

        if (servicesDropdown) {
            let hoverTimer = null;

            servicesDropdown.addEventListener("mouseenter", function () {
                if (window.innerWidth > 768) {
                    clearTimeout(hoverTimer);
                    setDropdownOpen(true);
                }
            });

            servicesDropdown.addEventListener("mouseleave", function () {
                if (window.innerWidth > 768) {
                    hoverTimer = setTimeout(function () {
                        setDropdownOpen(false);
                    }, 140);
                }
            });
        }

        document.addEventListener("click", function (e) {
            const target = e.target;
            if (servicesDropdown && !servicesDropdown.contains(target)) {
                setDropdownOpen(false);
            }
            if (navMenu && navMenu.classList.contains("active")) {
                const insideMenu = navMenu.contains(target);
                const onToggle = mobileMenuBtn && mobileMenuBtn.contains(target);
                if (!insideMenu && !onToggle) {
                    setDrawerOpen(false);
                }
            }
        });

        window.addEventListener("resize", function () {
            if (window.innerWidth > 768) {
                setDrawerOpen(false);
                setDropdownOpen(false);
            }
        });
    }

    function initScrollReveal() {
        const targets = document.querySelectorAll(".reveal-on-scroll, .fade-in");
        if (!targets.length) return;

        targets.forEach(function (el) {
            el.style.opacity = "1";
            el.style.transform = "translateY(0)";
            el.classList.add("is-visible", "visible", "active");
        });

        if ("IntersectionObserver" in window) {
            const observer = new IntersectionObserver(
                function (entries) {
                    entries.forEach(function (entry) {
                        if (entry.isIntersecting) {
                            entry.target.style.opacity = "1";
                            entry.target.style.transform = "translateY(0)";
                            entry.target.classList.add("is-visible", "visible", "active");
                        }
                    });
                },
                { threshold: 0.08 }
            );

            targets.forEach(function (el) {
                observer.observe(el);
            });
        }
    }

    function initGrowthCurveAndBars() {
        const chartBox = document.getElementById("growthChartBox");
        if (!chartBox) return;

        const curvePath = document.getElementById("hdCurvePath");
        const bars = chartBox.querySelectorAll(".bar-fill");

        function updateChart() {
            const rect = chartBox.getBoundingClientRect();
            const viewportH = window.innerHeight || document.documentElement.clientHeight;
            const totalSpan = viewportH + rect.height;
            const traveled = viewportH - rect.top;
            const ratio = Math.max(0, Math.min(1, traveled / Math.max(1, totalSpan * 0.65)));

            if (curvePath) {
                const pathLen =
                    typeof curvePath.getTotalLength === "function"
                        ? curvePath.getTotalLength()
                        : 1000;
                curvePath.style.strokeDasharray = String(pathLen);
                curvePath.style.strokeDashoffset = String(pathLen * (1 - ratio));
            }

            bars.forEach(function (bar) {
                const targetPct = parseFloat(bar.getAttribute("data-height")) || 0;
                const nextHeight = (targetPct * ratio).toFixed(1);
                bar.style.height = nextHeight + "%";

                if (ratio >= 0.35) {
                    bar.classList.add("active-value");
                } else {
                    bar.classList.remove("active-value");
                }
            });
        }

        window.addEventListener("scroll", updateChart, { passive: true });
        window.addEventListener("resize", updateChart, { passive: true });
        window.addEventListener("load", updateChart);
        updateChart();
    }

    document.addEventListener("DOMContentLoaded", function () {
        initNavigationAndDropdown();
        initScrollReveal();
        initGrowthCurveAndBars();
    });
})();