export const EXTRACTION_MODELS = [
  {
    id: 'meta.llama3-1-8b-instruct-v1:0',
    label: 'Llama 3.1 8B · US West',
  },
  {
    id: 'mistral.ministral-3-8b-instruct',
    label: 'Ministral 3 8B · Sydney',
  },
  {
    id: 'amazon.nova-2-lite-v1:0',
    label: 'Nova 2 Lite · Sydney',
  },
  {
    id: 'amazon.nova-lite-v1:0',
    label: 'Nova Lite · Sydney',
  },
];

export const DEFAULT_EXTRACTION_MODEL = EXTRACTION_MODELS[0].id;
