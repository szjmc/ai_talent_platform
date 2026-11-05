/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: { talent: { 50: '#f4f7ff', 100: '#eaf0ff', 500: '#5273df', 600: '#315efb', 700: '#284cc4' } },
      boxShadow: { resume: '0 12px 32px rgba(32, 50, 86, 0.09), 0 2px 8px rgba(32, 50, 86, 0.04)' },
    },
  },
  plugins: [],
};
