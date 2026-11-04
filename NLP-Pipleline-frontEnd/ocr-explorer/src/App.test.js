import { render, screen } from '@testing-library/react';

jest.mock('react-pdf', () => ({
  __esModule: true,
  Document: ({ children }) => <div>{children}</div>,
  Page: () => <div data-testid="pdf-page" />,
  pdfjs: { GlobalWorkerOptions: {} },
}));

jest.mock('react-pdf/dist/Page/AnnotationLayer.css', () => ({}), { virtual: true });
jest.mock('react-pdf/dist/Page/TextLayer.css', () => ({}), { virtual: true });

import App from './App';

beforeEach(() => {
  localStorage.setItem('breathe_auth', JSON.stringify({
    username: 'demo-user',
    role: 'admin',
    permissions: ['extract', 'history', 'database', 'taxonomy', 'llm', 'atlas'],
  }));
});

afterEach(() => {
  localStorage.clear();
});

test('renders the OCR upload workspace', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: /archive lens/i })).toBeInTheDocument();
  expect(screen.getByText(/drop a document here/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /extract text/i })).toBeDisabled();
});
