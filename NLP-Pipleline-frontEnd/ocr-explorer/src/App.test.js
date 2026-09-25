import { render, screen } from '@testing-library/react';
import App from './App';

test('renders the OCR upload workspace', () => {
  render(<App />);
  expect(screen.getByRole('heading', { name: /archive lens/i })).toBeInTheDocument();
  expect(screen.getByText(/drop a document here/i)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /extract text/i })).toBeDisabled();
});
