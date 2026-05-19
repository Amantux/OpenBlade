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
        quantum: {
          red: '#CC0000',
          'red-hover': '#a80000',
          navy: '#1e2738',
          sidebar: '#252d3a',
          panel: '#1a202c',
          north: '#1e2535',
          info: '#1a1f2e',
          status: '#141824',
          border: '#2d3748',
          'text-dim': '#94a3b8',
          selected: '#2b4f80',
          'selected-border': '#3b82f6',
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
