// Display only. Queries and exports retain the dataset's stable values.
export function displayValue(value: string) {
  return value.replace(/^Спринт\s+/i, 'Рабочий цикл ');
}
