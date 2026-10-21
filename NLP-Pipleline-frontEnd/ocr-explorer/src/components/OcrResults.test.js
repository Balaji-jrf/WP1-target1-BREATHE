import { fireEvent, render, screen } from '@testing-library/react';
import OcrResults from './OcrResults';

const result = {
  elements: [
    { page: 1, text: 'Page one text', confidence: 0.98, bbox: [0, 0, 1, 1] },
    { page: 2, text: 'Page two text', confidence: 0.95, bbox: [0, 0, 1, 1] },
  ],
  layout: [],
  tables: [],
  figures: [{
    page: 2,
    bbox: [0.1, 0.2, 0.4, 0.5],
    crop_status: 'saved',
    image_url: 'https://example.test/figure.png',
  }],
};

test('filters the shared OCR explorer to the controlled page', () => {
  const onPageFilterChange = jest.fn();
  const { rerender } = render(
    <OcrResults
      result={result}
      pageFilter="2"
      onPageFilterChange={onPageFilterChange}
    />,
  );

  expect(screen.getByText('Page two text')).toBeInTheDocument();
  expect(screen.queryByText('Page one text')).not.toBeInTheDocument();

  fireEvent.change(screen.getByLabelText('Page'), { target: { value: '1' } });
  expect(onPageFilterChange).toHaveBeenCalledWith('1');
  rerender(
    <OcrResults
      result={result}
      pageFilter="1"
      onPageFilterChange={onPageFilterChange}
    />,
  );
  expect(screen.getByText('Page one text')).toBeInTheDocument();
  expect(screen.queryByText('Page two text')).not.toBeInTheDocument();
});

test('renders a saved figure image in the Figures tab', () => {
  render(<OcrResults result={result} />);

  fireEvent.click(screen.getByRole('tab', { name: /figures/i }));
  expect(screen.getByRole('img', { name: /detected figure on page 02/i }))
    .toHaveAttribute('src', 'https://example.test/figure.png');
});
