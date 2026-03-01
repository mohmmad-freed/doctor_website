/**
 * Modern Trust - Landing Page Scripts
 * Handles Theme Toggle & Scroll Animations
 */

document.addEventListener('DOMContentLoaded', () => {
    const themeToggleBtn = document.getElementById('theme-toggle');

    // Check for saved theme preference, otherwise use system preference
    const getPreferredTheme = () => {
        const savedTheme = localStorage.getItem('theme');
        if (savedTheme) {
            return savedTheme;
        }
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    };

    const setTheme = (theme) => {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);
        updateToggleState(theme);
    };

    const updateToggleState = (theme) => {
        if (theme === 'dark') {
            themeToggleBtn.classList.add('is-dark');
        } else {
            themeToggleBtn.classList.remove('is-dark');
        }
    };

    // Initialize
    const currentTheme = getPreferredTheme();
    setTheme(currentTheme);

    // Event Listener
    themeToggleBtn.addEventListener('click', () => {
        const activeTheme = document.documentElement.getAttribute('data-theme');
        const newTheme = activeTheme === 'dark' ? 'light' : 'dark';
        setTheme(newTheme);
        setTheme(newTheme);
    });

    // =========================================
    // Mobile Menu Toggle
    // =========================================
    const mobileMenuBtn = document.querySelector('.mobile-menu-btn');
    const navbarMenu = document.querySelector('.navbar-menu');

    if (mobileMenuBtn && navbarMenu) {
        mobileMenuBtn.addEventListener('click', () => {
            navbarMenu.classList.toggle('is-active');

            // Optional: Toggle icon state (bars to xmark)
            const icon = mobileMenuBtn.querySelector('i');
            if (icon) {
                if (navbarMenu.classList.contains('is-active')) {
                    icon.classList.remove('fa-bars');
                    icon.classList.add('fa-xmark');
                } else {
                    icon.classList.remove('fa-xmark');
                    icon.classList.add('fa-bars');
                }
            }
        });
    }

    // =========================================
    // Scroll Reveal Interactions
    // =========================================
    const observerOptions = {
        threshold: 0.1,
        rootMargin: "0px 0px -50px 0px"
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('is-visible');
                observer.unobserve(entry.target); // Only animate once
            }
        });
    }, observerOptions);

    // const revealElements = document.querySelectorAll('.reveal-on-scroll');
    // revealElements.forEach(el => observer.observe(el));
});
