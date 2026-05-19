import type { Config } from 'tailwindcss';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'blade-950': '#0a0f1a',
        'blade-900': '#0f172a',
        'blade-800': '#1e293b',
      },
    },
  },
  plugins: [],
} satisfies Config;
