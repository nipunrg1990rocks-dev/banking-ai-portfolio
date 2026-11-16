import { Box } from "@mui/material";
import { DataGrid, type GridColDef, type GridColumnVisibilityModel, type GridPaginationModel, type GridSortModel } from "@mui/x-data-grid";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Page } from "../api";
import { ErrorAlert } from "./common";

type Props = {
  path: string;
  params?: Record<string, any>;
  columns: GridColDef[];
  rowId: string;
  onRowClick?: (row: any) => void;
  serverSort?: boolean;
  defaultSort?: string;
  pageSize?: number;
  height?: number | string;
  testId?: string;
  columnVisibility?: GridColumnVisibilityModel;
  onColumnVisibilityChange?: (m: GridColumnVisibilityModel) => void;
  onData?: (p: Page<any>) => void;
};

export default function ServerGrid({ path, params = {}, columns, rowId, onRowClick, serverSort, defaultSort, pageSize = 25, height = 560, testId, columnVisibility, onColumnVisibilityChange, onData }: Props) {
  const [pg, setPg] = useState<GridPaginationModel>({ page: 0, pageSize });
  const [sort, setSort] = useState<GridSortModel>([]);
  const sortParam = serverSort && sort[0] ? `${sort[0].sort === "desc" ? "-" : ""}${sort[0].field}` : defaultSort;
  const q = useQuery({
    queryKey: [path, params, pg, sortParam],
    queryFn: async () => {
      const r = await api<Page<any>>(path, { params: { ...params, page: pg.page + 1, page_size: pg.pageSize, sort: sortParam } });
      onData?.(r);
      return r;
    },
    placeholderData: (prev) => prev,
  });
  return (
    <Box sx={{ height, width: "100%" }} data-testid={testId}>
      <ErrorAlert error={q.error} />
      <DataGrid
        rows={q.data?.items || []}
        getRowId={(r) => r[rowId]}
        columns={columns}
        rowCount={q.data?.total ?? 0}
        loading={q.isFetching}
        paginationMode="server"
        sortingMode={serverSort ? "server" : "client"}
        paginationModel={pg}
        onPaginationModelChange={setPg}
        sortModel={sort}
        onSortModelChange={setSort}
        pageSizeOptions={[10, 25, 50, 100]}
        density="compact"
        disableRowSelectionOnClick
        onRowClick={onRowClick ? (p) => onRowClick(p.row) : undefined}
        columnVisibilityModel={columnVisibility}
        onColumnVisibilityModelChange={onColumnVisibilityChange}
        sx={{ "& .MuiDataGrid-row": { cursor: onRowClick ? "pointer" : "default" } }}
      />
    </Box>
  );
}
