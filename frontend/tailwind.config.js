/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'holding': {
          50: '#f0f4ff',
          100: '#dbe4ff',
          200: '#bac8ff',
          500: '#4c6ef5',
          600: '#3b5bdb',
          700: '#364fc7',
          900: '#1b2559',
        },
      },
    },
  },
  plugins: [],
}
