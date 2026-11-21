export const metadata = { title: 'Inbox Tend', description: 'Review work from email.' };

export default function RootLayout({ children }) {
  return <html lang="en"><head>
    <link rel="icon" href="/static/icons/favicon.svg" type="image/svg+xml" />
    <link rel="stylesheet" href="/static/vendor/fontawesome/css/fontawesome.min.css" />
    <link rel="stylesheet" href="/static/vendor/fontawesome/css/solid.min.css" />
    <link rel="stylesheet" href="/static/vendor/fontawesome/css/regular.min.css" />
    <link rel="stylesheet" href="/static/vendor/flag-icons/css/flag-icons.min.css" />
    <link rel="stylesheet" href="/static/styles.css" />
  </head><body id="top">{children}</body></html>;
}
