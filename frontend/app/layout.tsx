import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hora da Tarefa",
  description: "Foto → agenda → lembrete: organize a lição de casa dos filhos.",
  icons: { icon: "/favicon-64.png" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
